"""向 Emby / Jellyfin 推送演员的人物条目: 头像与简介 / 生日 / 出身地.

只推人物条目, 不碰影片元数据. 单个条目失败记入结果并继续处理下一个; 人物列表读不出来 (服务器不可达 /
认证失败) 才让整个任务失败.
"""

from __future__ import annotations

import mimetypes
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from ..emby import EmbyClient, EmbyError, EmbyPerson, match_persons
from ..utils.threads import in_thread
from .models import EmbySyncPayload, EmbySyncResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..config import HotSettings
    from ..db import Actor, Repository
    from ..media import ResourceStore

logger = structlog.get_logger()

_FAILURES_LIMIT = 50
"""结果里保留的失败明细条数; ``failed`` 计数不受此上限影响."""

_ACTOR_BATCH = 500
"""全量同步时逐页读取演员的条数."""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""服务器只接受完整的日期; 其它形态的生日不推送."""


@in_thread
def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


@dataclass
class _Counts:
    """逐演员累加的计数; 与 ``EmbySyncResult`` 的字段同名."""

    processed: int = 0
    """已完成的演员数; 结果里的 ``actors`` 用它, 因此取消时快照仍然自洽."""
    persons: int = 0
    images: int = 0
    updated: int = 0
    skipped: int = 0
    not_found: int = 0
    no_image: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


_counts_ctx: ContextVar[_Counts | None] = ContextVar("emby_sync_counts", default=None)
"""进行中任务的计数; ``failure_result`` 在取消与崩溃路径读它补交快照.

ContextVar 随任务上下文隔离, 同一 handler 实例并发执行多个人物同步时互不串数.
"""


def _task_success(counts: _Counts) -> bool:
    """逐条失败不改终态: 只要有条目写入成功就视为成功 (与刮削的站点部分失败同例);
    一条都没写成且存在失败时才算失败, 供用户重跑.
    """
    return counts.failed == 0 or counts.images + counts.updated > 0


class EmbySyncHandler(TaskHandler[EmbySyncPayload, EmbySyncResult]):
    def __init__(self, repo: Repository, resource_store: ResourceStore, config: HotSettings) -> None:
        super().__init__(payload_t=EmbySyncPayload, result_t=EmbySyncResult)
        self._repo = repo
        self._store = resource_store
        self._config = config

    async def handle(self, payload: EmbySyncPayload) -> TaskResult[EmbySyncResult]:
        # 计数先放进任务上下文: 之后任何一步抛异常, failure_result 都能补交已完成部分的快照.
        counts = _Counts()
        _counts_ctx.set(counts)

        cfg = self._config.emby
        if not cfg.enabled:
            return TaskResult(success=False, error="Emby / Jellyfin 未配置: 需要 emby.url 与 emby.api_key")

        if payload.actor_id is not None:
            actor = await self._repo.get_actor(payload.actor_id)
            if actor is None:
                return TaskResult(success=False, error=f"演员 {payload.actor_id} 不存在")
            actors = [actor]
        else:
            actors = await self._list_actors()

        async with EmbyClient(cfg) as client:
            try:
                # 单演员走检索: 每次演员刮削后只同步一个人, 为此拉全表不划算.
                index = await client.list_persons() if payload.actor_id is None else None
            except EmbyError as exc:
                return TaskResult(success=False, error=f"读取人物列表失败: {exc}")

            for done, actor in enumerate(actors, start=1):
                await self._sync_actor(actor, client, index, force=payload.force, counts=counts)
                counts.processed = done
                await self.report_progress(done, len(actors), actor.name)

        logger.info(
            "emby sync completed",
            actors=len(actors),
            persons=counts.persons,
            images=counts.images,
            updated=counts.updated,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            failed=counts.failed,
        )
        return TaskResult(success=_task_success(counts), result=self._result(counts, actors=len(actors)))

    def failure_result(self, payload: EmbySyncPayload) -> EmbySyncResult | None:
        """取消或崩溃时补交已完成部分的计数: 批量同步耗时长, 空白结果看不出推进到哪."""
        counts = _counts_ctx.get()
        if counts is None or counts.processed == 0:
            return None
        return self._result(counts, actors=counts.processed)

    @staticmethod
    def _result(counts: _Counts, *, actors: int) -> EmbySyncResult:
        return EmbySyncResult(
            actors=actors,
            persons=counts.persons,
            images=counts.images,
            updated=counts.updated,
            skipped=counts.skipped,
            not_found=counts.not_found,
            no_image=counts.no_image,
            failed=counts.failed,
            failures=counts.failures,
        )

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
        client: EmbyClient,
        index: list[EmbyPerson] | None,
        *,
        force: bool,
        counts: _Counts,
    ) -> None:
        names = [actor.name, *await self._repo.get_actor_aliases(actor.id or 0)]
        try:
            persons = await self._find_persons(client, index, names)
        except EmbyError as exc:
            counts.failed += 1
            self._record_failure(counts, actor.name, str(exc))
            return
        if not persons:
            counts.not_found += 1
            return

        portrait: tuple[str, bytes] | None = None
        if self._config.emby.sync_images:
            portrait = await self._portrait(actor)
            if portrait is None:
                counts.no_image += 1
        for person in persons:
            counts.persons += 1
            await self._sync_person(actor, person, portrait, client, force=force, counts=counts)

    async def _find_persons(
        self, client: EmbyClient, index: list[EmbyPerson] | None, names: Sequence[str]
    ) -> list[EmbyPerson]:
        """命中要按归一后的名字精确相等; 服务器的检索是模糊的, 因此结果还要过一遍匹配."""
        if index is not None:
            return match_persons(names, index)
        found: dict[str, EmbyPerson] = {}
        for name in names:
            for person in await client.search_persons(name):
                found.setdefault(person.id, person)
        return match_persons(names, found.values())

    async def _portrait(self, actor: Actor) -> tuple[str, bytes] | None:
        """头像取 ``image_urls`` 里第一个已落盘的资源; 文件缺失时继续试下一个 URL."""
        for url in actor.image_urls:
            record = await self._store.get_by_url(url)
            if record is None:
                continue
            path = self._store.full_path(record)
            try:
                data = await _read_bytes(path)
            except OSError as exc:
                logger.warning("emby sync: 头像文件读取失败", url=url, path=str(path), error=str(exc))
                continue
            mime = record.mime_type or mimetypes.guess_type(str(path))[0] or "image/jpeg"
            return mime, data
        return None

    async def _sync_person(
        self,
        actor: Actor,
        person: EmbyPerson,
        portrait: tuple[str, bytes] | None,
        client: EmbyClient,
        *,
        force: bool,
        counts: _Counts,
    ) -> None:
        overwrite = force or self._config.emby.overwrite
        wrote = False
        attempted = False

        if portrait is not None and (overwrite or not person.has_primary_image):
            mime, data = portrait
            attempted = True
            try:
                await client.upload_primary_image(person.id, data, content_type=mime)
            except EmbyError as exc:
                counts.failed += 1
                self._record_failure(counts, person.name, f"头像上传失败: {exc}")
            else:
                counts.images += 1
                wrote = True

        if self._config.emby.sync_profile:
            fields = self._profile_fields(actor, person, overwrite=overwrite)
            if fields:
                attempted = True
                try:
                    await client.update_person(person.id, fields)
                except EmbyError as exc:
                    counts.failed += 1
                    self._record_failure(counts, person.name, f"字段回写失败: {exc}")
                else:
                    counts.updated += 1
                    wrote = True

        # 失败的条目只计失败: 它既不是「已是最新」也已有明细
        if not wrote and not attempted:
            counts.skipped += 1

    @staticmethod
    def _profile_fields(actor: Actor, person: EmbyPerson, *, overwrite: bool) -> dict[str, object]:
        """只发要改的字段; 非覆盖模式下跳过服务器上已有值的字段."""
        fields: dict[str, object] = {}
        if actor.overview and (overwrite or not person.overview):
            fields["Overview"] = actor.overview
        # 生日用 PremiereDate 表达, 它不在 /Persons 的 fields 枚举内, 服务器上的现值读不回来:
        # 无法判断是否已有值时默认模式不写, 避免静默覆盖服务器上已有或手工填过的值.
        if actor.birthday and _ISO_DATE.match(actor.birthday) and overwrite:
            fields["PremiereDate"] = actor.birthday
        if actor.birthplace and (overwrite or not person.production_locations):
            fields["ProductionLocations"] = [actor.birthplace]
        return fields

    @staticmethod
    def _record_failure(counts: _Counts, name: str, reason: str) -> None:
        if len(counts.failures) < _FAILURES_LIMIT:
            counts.failures.append(f"{name}: {reason}")
