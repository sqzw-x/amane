"""把 Amane 的演员人物资料推送到 Emby / Jellyfin 的人物条目.

只写人物条目, 不碰影片元数据. 更新必须带上写回白名单中的当前值: 服务器按替换语义处理未提交字段.
单个条目失败记入结果并继续; 人物列表读不出来 (不可达 / 认证失败) 才让整个任务失败.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from ..media.file_type import IMAGE_MIME_PREFIX, detect_media_type_bytes
from ..media_server import (
    SEARCH_LIMIT,
    MediaPerson,
    MediaServerClient,
    MediaServerClientLike,
    MediaServerError,
    find_persons,
    index_persons,
    match_persons,
)
from ..utils.collections import unique_ids
from ..utils.threads import in_thread
from .models import ActorPushPayload, ActorPushResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ..config import HotSettings, MediaServerConfig
    from ..db.models import Actor
    from ..db.repository import Repository
    from ..media import ResourceStore

logger = structlog.get_logger()

_FAILURES_LIMIT = 50
"""结果里保留的失败明细条数; ``failed`` 计数不受此上限影响."""

_ACTOR_BATCH = 500
"""全量推送时逐页读取演员的条数."""

_SEARCH_THRESHOLD = 50
"""ID 列表不超过该值时逐名检索; 更大或全量时一次读回人物表建索引."""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class _Counts:
    requested: int = 0
    matched: int = 0
    updated: int = 0
    images: int = 0
    skipped: int = 0
    not_found: int = 0
    no_image: int = 0
    missing: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def failure(self, name: str, reason: str) -> None:
        if len(self.failures) < _FAILURES_LIMIT:
            self.failures.append(f"{name}: {reason}")


@dataclass(frozen=True)
class _Portrait:
    """已落盘且类型可用的头像."""

    data: bytes
    mime: str


@dataclass(frozen=True)
class _UnrecognizedPortrait:
    """已落盘但不是可识别的图片: 上传只会被服务器拒绝, 不发送."""

    url: str


_PortraitSource = _Portrait | _UnrecognizedPortrait


@in_thread
def _read_portrait(path: Path) -> tuple[bytes, str | None]:
    """读出头像字节并判定媒体类型; PIL 解析同样放在线程里, 不占用事件循环."""
    data = path.read_bytes()
    return data, detect_media_type_bytes(data)


class ActorPushHandler(TaskHandler[ActorPushPayload, ActorPushResult]):
    def __init__(
        self,
        repo: Repository,
        resource_store: ResourceStore,
        config: HotSettings,
        client_factory: Callable[[MediaServerConfig], MediaServerClientLike] = MediaServerClient,
    ) -> None:
        super().__init__(payload_t=ActorPushPayload, result_t=ActorPushResult)
        self._repo = repo
        self._resource_store = resource_store
        self._config = config
        self._client_factory = client_factory

    async def handle(self, payload: ActorPushPayload) -> TaskResult[ActorPushResult]:
        cfg = self._config.media_server
        if not cfg.enabled:
            return TaskResult(success=False, error="媒体服务器未配置: 需要 media_server.url 与 media_server.api_key")

        counts = _Counts()
        if payload.actor_ids:
            ids = unique_ids(payload.actor_ids)
            counts.requested = len(ids)
            actors = await self._repo.get_actors(ids)
            counts.missing = counts.requested - len(actors)
        else:
            actors = await self._list_actors()
            counts.requested = len(actors)

        if not actors:
            return TaskResult(success=True, result=self._result(counts))

        actor_ids = [actor.id for actor in actors if actor.id is not None]
        aliases = await self._repo.get_actor_aliases_map(None if not payload.actor_ids else actor_ids)

        index = None
        seen_persons: set[str] = set()
        async with self._client_factory(cfg) as client:
            if not payload.actor_ids or len(actors) > _SEARCH_THRESHOLD:
                try:
                    persons = await client.list_persons()
                except MediaServerError as exc:
                    return TaskResult(
                        success=False,
                        error=f"读取人物列表失败: {exc}",
                        result=self._result(counts),
                    )
                index = index_persons(persons)

            for done, actor in enumerate(actors, start=1):
                await self._sync_actor(
                    actor,
                    client,
                    index,
                    aliases.get(actor.id or 0, []),
                    seen_persons=seen_persons,
                    force=payload.force,
                    counts=counts,
                )
                await self.report_progress(done, len(actors), actor.name)

        success = counts.failed == 0 or counts.updated + counts.images > 0
        logger.info(
            "actor push completed",
            kind=cfg.kind,
            requested=counts.requested,
            matched=counts.matched,
            updated=counts.updated,
            images=counts.images,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            missing=counts.missing,
            failed=counts.failed,
        )
        return TaskResult(success=success, result=self._result(counts))

    async def _list_actors(self) -> list[Actor]:
        actors: list[Actor] = []
        offset = 0
        while True:
            page = await self._repo.list_actors(offset=offset, limit=_ACTOR_BATCH)
            actors.extend(page)
            if len(page) < _ACTOR_BATCH:
                return actors
            offset += _ACTOR_BATCH

    async def _sync_actor(
        self,
        actor: Actor,
        client: MediaServerClientLike,
        index: dict[str, list[MediaPerson]] | None,
        aliases: Sequence[str],
        *,
        seen_persons: set[str],
        force: bool,
        counts: _Counts,
    ) -> None:
        names = [actor.name, *aliases]
        try:
            persons = await self._find_persons(client, index, names)
        except MediaServerError as exc:
            counts.failed += 1
            counts.failure(actor.name, f"查找人物失败: {exc}")
            return
        if not persons:
            counts.not_found += 1
            return

        portrait = await self._portrait(actor)
        if portrait is None:
            counts.no_image += 1

        for person in persons:
            if person.id in seen_persons:
                continue
            seen_persons.add(person.id)
            counts.matched += 1
            await self._sync_person(actor, person, portrait, client, force=force, counts=counts)

    async def _find_persons(
        self,
        client: MediaServerClientLike,
        index: dict[str, list[MediaPerson]] | None,
        names: Sequence[str],
    ) -> list[MediaPerson]:
        if index is not None:
            return find_persons(index, names)
        candidates: dict[str, MediaPerson] = {}
        for name in names:
            results = await client.search_persons(name)
            if len(results) >= SEARCH_LIMIT:
                # 模糊候选已到单页上限, 精确匹配可能被截断; 回退到全量索引.
                return find_persons(index_persons(await client.list_persons()), names)
            for person in results:
                candidates.setdefault(person.id, person)
        return match_persons(names, candidates.values())

    async def _portrait(self, actor: Actor) -> _PortraitSource | None:
        """头像取 ``image_urls`` 里第一个已落盘且内容判为图片的资源; 不在推送任务里补下载.

        记录里的媒体类型不参与: 旧数据由 URL 后缀推出, 与字节无关. 文件读取失败与未落盘同样
        跳过 (最终计 ``no_image``); 内容判不出时不发送, 返回 ``_UnrecognizedPortrait`` 记失败.
        """
        unrecognized: str | None = None
        for url in actor.image_urls:
            record = await self._resource_store.get_by_url(url)
            if record is None:
                continue
            path = self._resource_store.full_path(record)
            try:
                data, mime = await _read_portrait(path)
            except OSError as exc:
                logger.warning("actor push: 头像文件读取失败", url=url, path=str(path), error=str(exc))
                continue
            if mime is None or not mime.startswith(IMAGE_MIME_PREFIX):
                logger.warning("actor push: 头像不是可识别的图片", url=url, path=str(path), mime=mime)
                unrecognized = unrecognized or url
                continue
            return _Portrait(data=data, mime=mime)
        return _UnrecognizedPortrait(url=unrecognized) if unrecognized else None

    async def _sync_person(
        self,
        actor: Actor,
        person: MediaPerson,
        portrait: _PortraitSource | None,
        client: MediaServerClientLike,
        *,
        force: bool,
        counts: _Counts,
    ) -> None:
        updates = _profile_updates(actor, person, force=force)
        needs_image = portrait is not None and (force or not person.has_primary_image)
        if not updates and not needs_image:
            counts.skipped += 1
            return

        person_failed = False
        if updates:
            try:
                await client.update_person(person, updates)
            except MediaServerError as exc:
                counts.failed += 1
                person_failed = True
                counts.failure(f"{actor.name} / {person.name}", f"字段写入失败: {exc}")
            else:
                counts.updated += 1

        if needs_image:
            assert portrait is not None
            error: str | None = None
            if isinstance(portrait, _UnrecognizedPortrait):
                error = f"头像格式无法识别: {portrait.url}"
            else:
                try:
                    await client.upload_primary_image(person.id, portrait.data, content_type=portrait.mime)
                except MediaServerError as exc:
                    error = f"头像上传失败: {exc}"
                else:
                    counts.images += 1
            if error is not None:
                if not person_failed:
                    counts.failed += 1
                counts.failure(f"{actor.name} / {person.name}", error)

    @staticmethod
    def _result(counts: _Counts) -> ActorPushResult:
        return ActorPushResult(
            requested=counts.requested,
            matched=counts.matched,
            updated=counts.updated,
            images=counts.images,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            missing=counts.missing,
            failed=counts.failed,
            failures=counts.failures,
        )


def _profile_updates(actor: Actor, person: MediaPerson, *, force: bool) -> dict[str, object]:
    """按覆盖规则给出目标字段; 不把空值写成清空动作."""
    updates: dict[str, object] = {}
    if actor.overview and (force or not _has_text(person.overview)):
        updates["Overview"] = actor.overview

    if actor.birthday and _ISO_DATE.fullmatch(actor.birthday):
        if force or not person.premiere_date:
            updates["PremiereDate"] = actor.birthday
        if force or person.production_year is None:
            updates["ProductionYear"] = int(actor.birthday[:4])

    if actor.birthplace and (force or not _has_text_list(person.production_locations)):
        updates["ProductionLocations"] = [actor.birthplace]
    return updates


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _has_text_list(values: Sequence[str]) -> bool:
    return any(value.strip() for value in values)
