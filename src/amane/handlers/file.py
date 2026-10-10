import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings, WatermarkConfig
from ..db.models import Library, MediaFile
from ..db.repo_types import MediaFileUpdates
from ..enums import ActorGender, DownloadableResource, LinkMode
from ..library import MEDIA_EXTENSIONS, LibraryFileKind, LibraryScan, ancestor_dirs, prune_empty_dirs
from ..library.rules import is_in_trash
from ..media import ResourceStore, apply_cover_watermarks_from_info, crop_poster
from ..media import write_nfo as write_nfo_file
from ..media.resource_store import internal_url_hash
from ..net.http import WebClient
from ..observability.models import OrganizeConflict, OrganizeConflictReason
from ..organize import (
    MoveMode,
    PlaceOutcome,
    ResolvedPaths,
    SubtitleConflict,
    TargetState,
    discover_subtitles,
    execute_organize,
    place_subtitles,
    render_strm_content,
    resolve_paths,
    target_state,
    video_dest,
)
from ..organize.link import create_video_link
from ..parsing import FileInfo, parse_file_info
from ..utils.path import existing_disk_path as existing_disk_path_sync
from ..utils.path import is_descendant, nfc_path, path_is_under
from ..utils.threads import existing_disk_path, in_thread, path_is_dir
from ._common import LibraryTaskLocks, refresh_external_subtitle
from .models import (
    ORGANIZE_CONFLICT_LIMIT,
    CleanupPayload,
    CleanupResult,
    OrganizePayload,
    OrganizeResult,
)
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import Metadata
    from ..db.repository import Repository
    from ..media import ResourceStore
    from ..net.http import WebClient

logger = structlog.get_logger()


@dataclass
class FileOperationsResult:
    outcome: PlaceOutcome
    dest: Path | None = None
    """PLACED 是落盘路径; FAILED 时可能仍带路径, 以便回写 MediaFile.path 补链接."""
    conflict_target: Path | None = None
    """CONFLICT 时被占用的目标路径."""
    subtitle_conflicts: list[SubtitleConflict] = field(default_factory=list)
    error: str | None = None


async def execute_file_operations(
    media_file: MediaFile,
    metadata: Metadata,
    paths: ResolvedPaths,
    move_mode: MoveMode,
    resource_store: ResourceStore,
    download_images: bool = True,
    write_nfo: bool = True,
    copy_resources: Sequence[DownloadableResource] | None = None,
    web_client: WebClient | None = None,
    config: HotSettings | None = None,
    library: Library | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
    watermark_dir: Path | None = None,
    actor_genders: dict[str, ActorGender] | None = None,
) -> FileOperationsResult:
    source_path = await existing_disk_path(Path(media_file.path))
    if source_path is None:
        logger.warning("source file missing", path=media_file.path)
        return FileOperationsResult(outcome=PlaceOutcome.FAILED, error=f"源文件不存在: {media_file.path}")

    info = file_info if file_info is not None else parse_file_info(source_path)

    # 目标被别的文件占用时立即返回: 不下载图片, 不写附属文件, 不改库内任何路径.
    # 读不出目标状态时同样立即返回, 但记为失败而不是冲突.
    # 目标路径必须与 execute_organize 同一算法: 模板写死扩展名时, 模板渲染结果与真实目标不同.
    dest = video_dest(paths.video.parent, paths.video.stem, source_path)
    check = await target_state(source_path, dest)
    if check.state is TargetState.OCCUPIED:
        logger.warning("organize target occupied", source=str(source_path), dest=str(dest))
        return FileOperationsResult(outcome=PlaceOutcome.CONFLICT, conflict_target=dest)
    if check.state is TargetState.UNKNOWN:
        logger.warning("organize target state unknown", source=str(source_path), dest=str(dest), error=check.error)
        return FileOperationsResult(outcome=PlaceOutcome.FAILED, error=check.error)

    # 下载图片; 水印打在库路径副本上, 不能修改 Resource 原图.
    if web_client and download_images:
        logger.debug("downloading images via store", number=metadata.number)
        kinds = set(copy_resources) if copy_resources is not None else set(DownloadableResource)
        await _download_images_via_store(
            metadata,
            resource_store,
            web_client,
            paths,
            config,
            kinds,
            file_info=info,
            watermark_dir=watermark_dir,
        )

    # 发现字幕: 必须在视频移动前检查同目录.
    subtitles: list[Path] = []
    if library is not None:
        subtitles = await discover_subtitles(source_path, library.subtitle_extensions, info)

    org_result = await execute_organize(
        source=source_path,
        target_dir=paths.video.parent,
        target_stem=paths.video.stem,
        mode=move_mode,
    )

    if org_result.outcome is PlaceOutcome.CONFLICT:
        return FileOperationsResult(outcome=PlaceOutcome.CONFLICT, conflict_target=org_result.dest)

    # 写链接; 失败仍带 dest, 以便回写 MediaFile.path.
    if org_result.outcome is PlaceOutcome.PLACED and org_result.dest and paths.link is not None:
        mode = LinkMode(library.link_mode) if library is not None else LinkMode.STRM
        strm_content: str | None = None
        if mode == LinkMode.STRM and library is not None:
            try:
                strm_content = render_strm_content(
                    library.strm_content_template,
                    org_result.dest,
                    Path(library.path),
                    metadata,
                    source_path=source_path,
                    file_info=info,
                    link=paths.link,
                    actor_genders=actor_genders,
                )
            except ValueError as e:
                return FileOperationsResult(outcome=PlaceOutcome.FAILED, dest=org_result.dest, error=str(e))
        link_result = await create_video_link(org_result.dest, paths.link, mode, content=strm_content)
        if link_result.outcome is not PlaceOutcome.PLACED:
            return FileOperationsResult(outcome=PlaceOutcome.FAILED, dest=org_result.dest, error=link_result.error)

    # 写入 NFO.
    if org_result.outcome is PlaceOutcome.PLACED and org_result.dest and write_nfo:
        await write_nfo_file(metadata, paths.nfo)

    # 字幕按模板落到 video_dest 侧.
    subtitle_conflicts: list[SubtitleConflict] = []
    if org_result.outcome is PlaceOutcome.PLACED and org_result.dest and library is not None and subtitles:
        subtitle_conflicts = await place_subtitles(
            subtitles,
            video_source=source_path,
            video_dest=org_result.dest,
            library=library,
            metadata=metadata,
            file_info=info,
            mode=move_mode,
            safe_dirs=safe_dirs,
            link_dir=paths.link.parent if paths.link is not None else None,
            link_name=paths.link.stem if paths.link is not None else None,
            actor_genders=actor_genders,
        )

    if org_result.outcome is PlaceOutcome.PLACED:
        logger.debug("file operations done", number=metadata.number, dest=str(org_result.dest), mode=str(move_mode))
        return FileOperationsResult(
            outcome=PlaceOutcome.PLACED, dest=org_result.dest, subtitle_conflicts=subtitle_conflicts
        )
    logger.debug("file operations failed", number=metadata.number, error=org_result.error)
    return FileOperationsResult(outcome=PlaceOutcome.FAILED, error=org_result.error)


async def apply_file_operations(
    repo: Repository,
    media_file_id: int | None,
    metadata: Metadata,
    config: HotSettings,
    resource_store: ResourceStore,
    *,
    write_nfo: bool = True,
    copy_resources: Sequence[DownloadableResource] | None = None,
    web_client: WebClient | None = None,
    safe_dirs: Sequence[Path] | None = (),
    watermark_dir: Path | None = None,
) -> FileOperationsResult | None:
    """缺少 media_file_id 或对应记录时返回 None (跳过, 不视为失败).
    Library 由 MediaFile.library_id 派生.
    """
    if media_file_id is None:
        return None
    media_file = await repo.get_media_file(media_file_id)
    if media_file is None:
        return None
    library = await repo.get_library(media_file.library_id)
    if library is None:
        return None

    # 渲染路径后执行落盘.
    ext = Path(media_file.path).suffix.lstrip(".")
    file_info = parse_file_info(media_file.path)
    actor_genders = {a.name: a.gender for a in await repo.get_actors_by_names(metadata.actors)}
    paths = resolve_paths(
        library,
        metadata,
        ext=ext,
        file_info=file_info,
        source_path=Path(media_file.path),
        safe_dirs=safe_dirs,
        actor_genders=actor_genders,
    )
    return await execute_file_operations(
        media_file=media_file,
        metadata=metadata,
        paths=paths,
        move_mode=library.move_mode,
        resource_store=resource_store,
        download_images=True,
        write_nfo=write_nfo,
        copy_resources=copy_resources,
        web_client=web_client,
        config=config,
        library=library,
        file_info=file_info,
        safe_dirs=safe_dirs,
        watermark_dir=watermark_dir,
        actor_genders=actor_genders,
    )


async def commit_organized_media_file(
    repo: Repository,
    media: MediaFile,
    placed: Path,
    library_root: Path,
) -> None:
    """整理后的路径仍在本库内才改 path; 已离开本库且源路径不在磁盘上则删行.

    目标路径已被另一行占用时删本行; 占用行缺少刮削字段则从本行补上.
    """
    if media.id is None:
        return
    library = await repo.get_library(media.library_id)
    if not is_descendant(placed, library_root):
        if await existing_disk_path(Path(media.path), follow_symlinks=False) is None:
            await repo.delete_media_file(media.id)
        return

    occupant = await repo.get_media_file_by_path(str(placed))
    if occupant is None or occupant.id == media.id:
        updated = await repo.update_media_file(media.id, path=str(placed))
        if updated is not None and library is not None:
            await refresh_external_subtitle(repo, updated, library)
        return
    if occupant.id is None:
        return
    occupant_updates: MediaFileUpdates = {}
    if occupant.metadata_id is None and media.metadata_id is not None:
        occupant_updates["metadata_id"] = media.metadata_id
        occupant_updates["status"] = media.status
    if occupant.oshash is None and media.oshash is not None:
        occupant_updates["oshash"] = media.oshash
    if occupant.number is None and media.number is not None:
        occupant_updates["number"] = media.number
    if occupant_updates:
        await repo.update_media_file(occupant.id, **occupant_updates)
    await repo.delete_media_file(media.id)
    if library is not None:
        await refresh_external_subtitle(repo, occupant, library)


async def _resolve_local(url: str, store: ResourceStore, client: WebClient) -> Path | None:
    """内部 `/api/resources/{hash}` 与外部 URL 均由 store 解析 (命中缓存直出)."""
    return await store.acquire(url, client)


async def _acquire_first_local(urls: list[str], store: ResourceStore, client: WebClient) -> Path | None:
    for url in urls:
        path = await _resolve_local(url, store, client)
        if path:
            return path
    return None


@in_thread
def _place_library_images(
    paths: ResolvedPaths,
    *,
    thumb_local: Path | None,
    poster_local: Path | None,
    trailer_local: Path | None,
    extrafanart: Sequence[Path],
    kinds: set[DownloadableResource],
    config: HotSettings | None,
    file_info: FileInfo | None,
    watermark_dir: Path | None,
) -> None:
    # 复制封面 / 海报 / 预告片 / extrafanart 到库路径.
    if thumb_local is not None and DownloadableResource.thumb in kinds:
        paths.thumb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(thumb_local, paths.thumb)
        paths.fanart.parent.mkdir(parents=True, exist_ok=True)
        if not paths.fanart.exists():
            shutil.copy2(thumb_local, paths.fanart)

    if DownloadableResource.poster in kinds:
        if poster_local is not None:
            paths.poster.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(poster_local, paths.poster)
        elif thumb_local is not None and config is not None and config.scraping.crop_poster:
            paths.poster.parent.mkdir(parents=True, exist_ok=True)
            crop_poster(
                paths.thumb,
                paths.poster,
                poster_ratio=config.scraping.poster_ratio,
                jpeg_quality=config.scraping.jpeg_quality,
            )

    if trailer_local is not None and DownloadableResource.trailer in kinds:
        paths.trailer.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(trailer_local, paths.trailer)

    if extrafanart and DownloadableResource.extrafanart in kinds:
        paths.extrafanart_dir.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(extrafanart):
            shutil.copy2(p, paths.extrafanart_dir / f"{i + 1}.jpg")

    # 叠加水印 (thumb / poster).
    wm = config.watermark if config is not None else WatermarkConfig()
    if wm.enabled and file_info is not None:
        jpeg_quality = config.scraping.jpeg_quality if config is not None else 95
        if paths.thumb.exists():
            apply_cover_watermarks_from_info(
                paths.thumb,
                file_info,
                jpeg_quality=jpeg_quality,
                watermark_dir=watermark_dir,
                scale=wm.scale,
                corners=wm.corners,
            )
        if paths.poster.exists():
            apply_cover_watermarks_from_info(
                paths.poster,
                file_info,
                jpeg_quality=jpeg_quality,
                watermark_dir=watermark_dir,
                scale=wm.scale,
                corners=wm.corners,
            )


async def _download_images_via_store(
    metadata: Metadata,
    store: ResourceStore,
    client: WebClient,
    paths: ResolvedPaths,
    config: HotSettings | None,
    kinds: set[DownloadableResource],
    file_info: FileInfo | None = None,
    watermark_dir: Path | None = None,
) -> None:
    """优先用 Resource 已有文件; 缺失则现场 acquire."""
    thumb_local = None
    if DownloadableResource.thumb in kinds:
        thumb_urls = metadata.thumb_urls
        thumb_local = await _acquire_first_local(thumb_urls, store, client) if thumb_urls else None

    poster_local = None
    if DownloadableResource.poster in kinds:
        poster_urls = metadata.poster_urls
        poster_local = await _acquire_first_local(poster_urls, store, client) if poster_urls else None

    trailer_local = None
    if DownloadableResource.trailer in kinds:
        trailer_urls = metadata.trailer_urls
        trailer_local = await _acquire_first_local(trailer_urls, store, client) if trailer_urls else None

    extrafanart: list[Path] = []
    if DownloadableResource.extrafanart in kinds:
        extrafanart_by_site = metadata.extrafanart_urls
        if extrafanart_by_site:
            priority = list(extrafanart_by_site.keys())
            extrafanart = await store.acquire_extrafanart(extrafanart_by_site, priority, client)

    await _place_library_images(
        paths,
        thumb_local=thumb_local,
        poster_local=poster_local,
        trailer_local=trailer_local,
        extrafanart=extrafanart,
        kinds=kinds,
        config=config,
        file_info=file_info,
        watermark_dir=watermark_dir,
    )


@in_thread
def _classify_indexed(path: Path, scan: LibraryScan) -> LibraryFileKind | None:
    return scan.classify(path)


class OrganizeHandler(TaskHandler[OrganizePayload, OrganizeResult]):
    """依据已有 Metadata 整理范围内的 MediaFile; 不刮削, 不修改 Metadata, 不扫描磁盘.

    同库执行期与 DELETE 共用一把锁.
    """

    def __init__(
        self,
        repo: Repository,
        config: HotSettings,
        resource_store: ResourceStore,
        web_client: WebClient | None = None,
        safe_dirs: Sequence[Path] | None = (),
        watermark_dir: Path | None = None,
        *,
        library_locks: LibraryTaskLocks | None = None,
    ):
        super().__init__(payload_t=OrganizePayload, result_t=OrganizeResult)
        self._repo = repo
        self._config = config
        self._web_client = web_client
        self._resource_store = resource_store
        self._safe_dirs = safe_dirs
        self._watermark_dir = watermark_dir
        self._library_locks = library_locks if library_locks is not None else LibraryTaskLocks()

    async def _load_scope(self, payload: OrganizePayload, library: Library) -> list[MediaFile]:
        assert library.id is not None
        if payload.media_file_ids is not None:
            return await self._repo.list_media_files(library_id=library.id, ids=payload.media_file_ids, limit=None)
        indexed = await self._repo.list_media_files(library_id=library.id, limit=None)
        if nfc_path(payload.path) != nfc_path(library.path):
            return [mf for mf in indexed if path_is_under(mf.path, payload.path)]
        return indexed

    async def handle(self, payload: OrganizePayload) -> TaskResult[OrganizeResult]:
        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"媒体库 {payload.library_id} 不存在")
        assert library.id is not None
        library_root = Path(library.path)
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"不是目录: {library.path}")
        if payload.media_file_ids is None:
            scope = Path(payload.path) if payload.path else library_root
            if nfc_path(str(scope)) != nfc_path(library.path) and not await path_is_dir(scope):
                return TaskResult(success=False, error=f"不是目录: {scope}")

        lock = await self._library_locks.get(library.id)
        async with lock:
            return await self._handle_unlocked(payload, library)

    async def _handle_unlocked(self, payload: OrganizePayload, library: Library) -> TaskResult[OrganizeResult]:
        indexed = await self._load_scope(payload, library)
        library_root = Path(library.path)
        media_extensions = frozenset(self._config.watcher.media_extensions) or MEDIA_EXTENSIONS
        scan = LibraryScan(
            trailer_pattern=library.trailer_pattern,
            blacklist_patterns=library.blacklist_patterns,
            min_file_size=library.min_file_size,
            media_extensions=media_extensions,
        )
        live: list[MediaFile] = []
        skipped = 0
        prune_total = len(indexed)
        if prune_total:
            await self.report_progress(0, prune_total, "prune")
            for i, mf in enumerate(indexed, start=1):
                if mf.id is not None:
                    mf_path = Path(mf.path)
                    missing = await existing_disk_path(mf_path, follow_symlinks=False) is None
                    if missing or not is_descendant(mf_path, library_root) or is_in_trash(mf_path):
                        await self._repo.delete_media_file(mf.id)
                    else:
                        kind = await _classify_indexed(mf_path, scan)
                        if kind is LibraryFileKind.UNWANTED or kind is LibraryFileKind.SKIP:
                            skipped += 1
                        else:
                            # MEDIA, 以及 classify 返回 None 的已索引行 (不在当前扩展名白名单,
                            # 例如库曾用 patterns 收过 .txt). 仍按索引落盘, 不按扫描 glob 再裁一次.
                            live.append(mf)
                await self.report_progress(i, prune_total, "prune")
        else:
            live = indexed

        organized = 0
        conflicted = 0
        conflicts: list[OrganizeConflict] = []
        failed = 0
        total = len(live)
        # 只有移动方式会移走源文件, 复制 / 硬链接 / 符号链接不腾空目录.
        prune_candidates: set[Path] = set()
        collect_prune = payload.prune_empty_dirs and library.move_mode is MoveMode.MOVE
        if total == 0:
            await self.report_progress(1, 1, "done")
        else:
            await self.report_progress(0, total, "organize")
            for i, media_file in enumerate(live, start=1):
                path_str = media_file.path
                file_path = Path(path_str)
                if media_file.metadata_id is None:
                    skipped += 1
                    await self.report_progress(i, total, file_path.name)
                    continue

                metadata = await self._repo.get_metadata(media_file.metadata_id)
                if metadata is None:
                    skipped += 1
                    await self.report_progress(i, total, file_path.name)
                    continue

                write_nfo = library.write_nfo if payload.write_nfo is None else payload.write_nfo
                copy_resources = library.copy_resources if payload.copy_resources is None else payload.copy_resources
                fop_result = await apply_file_operations(
                    self._repo,
                    media_file.id,
                    metadata,
                    self._config,
                    self._resource_store,
                    write_nfo=write_nfo,
                    copy_resources=copy_resources,
                    web_client=self._web_client,
                    safe_dirs=self._safe_dirs,
                    watermark_dir=self._watermark_dir,
                )
                if fop_result is None:
                    skipped += 1
                    await self.report_progress(i, total, file_path.name)
                    continue

                if fop_result.dest and media_file.id is not None:
                    await commit_organized_media_file(self._repo, media_file, fop_result.dest, library_root)
                match fop_result.outcome:
                    case PlaceOutcome.PLACED:
                        organized += 1
                        if collect_prune:
                            prune_candidates.update(ancestor_dirs(file_path, library_root=library_root))
                        # 字幕冲突的 path 是留在原处的那条字幕, 不是视频源路径.
                        conflicted += _record_conflicts(
                            conflicts,
                            [
                                OrganizeConflict(
                                    path=str(item.source),
                                    target=str(item.target),
                                    reason=OrganizeConflictReason.subtitle_target_exists,
                                )
                                for item in fop_result.subtitle_conflicts
                            ],
                        )
                    case PlaceOutcome.CONFLICT:
                        # 行不提交新路径, 留在源路径: 下次整理会再次报告, 直到用户自行处理.
                        target = fop_result.conflict_target
                        conflicted += _record_conflicts(
                            conflicts,
                            []
                            if target is None
                            else [
                                OrganizeConflict(
                                    path=path_str, target=str(target), reason=OrganizeConflictReason.target_exists
                                )
                            ],
                        )
                    case PlaceOutcome.FAILED:
                        logger.warning("organize failed", path=path_str, error=fop_result.error)
                        failed += 1
                await self.report_progress(i, total, file_path.name)
            await self.report_progress(total, total, "done")

        pruned_dirs = 0
        if prune_candidates:
            pruned_dirs = (await prune_empty_dirs(prune_candidates, library_root=library_root)).removed

        logger.info(
            "organize completed",
            path=payload.path,
            organized=organized,
            skipped=skipped,
            conflicted=conflicted,
            failed=failed,
            pruned_dirs=pruned_dirs,
        )

        return TaskResult(
            True,
            result=OrganizeResult(
                organized=organized,
                skipped=skipped,
                conflicted=conflicted,
                failed=failed,
                pruned_dirs=pruned_dirs,
                conflicts=conflicts,
            ),
        )


def _record_conflicts(conflicts: list[OrganizeConflict], entries: Sequence[OrganizeConflict]) -> int:
    """登记未处理的目标, 返回条数; 列表达到上限后只计数, 差额只留在任务日志里."""
    conflicts.extend(entries[: max(0, ORGANIZE_CONFLICT_LIMIT - len(conflicts))])
    return len(entries)


def _add_resource_ref(url: str, live_urls: set[str], live_hashes: set[str]) -> None:
    if not url:
        return
    url_hash = internal_url_hash(url)
    if url_hash is not None:
        live_hashes.add(url_hash)
    else:
        live_urls.add(url)


async def _collect_live_resource_refs(repo: Repository) -> tuple[set[str], set[str]]:
    live_urls: set[str] = set()
    live_hashes: set[str] = set()
    offset = 0
    page = 500
    # 收集 Metadata 仍引用的 locator / url_hash.
    while True:
        batch, _total = await repo.list_metadata(offset=offset, limit=page)
        if not batch:
            break
        for meta in batch:
            for u in (*meta.poster_urls, *meta.thumb_urls, *meta.trailer_urls):
                _add_resource_ref(u, live_urls, live_hashes)
            for site_urls in meta.extrafanart_urls.values():
                for u in site_urls:
                    _add_resource_ref(u, live_urls, live_hashes)
        offset += len(batch)
        if len(batch) < page:
            break

    offset = 0
    # 收集 Actor 头像 URL.
    while True:
        actors = await repo.list_actors(offset=offset, limit=page)
        if not actors:
            break
        for actor in actors:
            for u in actor.image_urls or []:
                _add_resource_ref(u, live_urls, live_hashes)
        offset += len(actors)
        if len(actors) < page:
            break
    return live_urls, live_hashes


@in_thread
def _missing_media_ids(rows: Sequence[tuple[int, str]]) -> list[int]:
    """不跟随符号链接判断存在."""
    return [media_id for media_id, path in rows if existing_disk_path_sync(path, follow_symlinks=False) is None]


class CleanupHandler(TaskHandler[CleanupPayload, CleanupResult]):
    """不因缺少 MediaFile 而删除 Metadata."""

    def __init__(self, repo: Repository, resource_store: ResourceStore):
        super().__init__(payload_t=CleanupPayload, result_t=CleanupResult)
        self._repo = repo
        self._resource_store = resource_store

    async def handle(self, payload: CleanupPayload) -> TaskResult[CleanupResult]:
        files_removed = 0
        resources_removed = 0

        # 删除磁盘上不存在的 MediaFile 记录.
        if payload.remove_missing_files:
            missing_ids: list[int] = []
            offset = 0
            page = 500
            while True:
                batch = await self._repo.list_media_files(limit=page, offset=offset)
                if not batch:
                    break
                missing_ids.extend(await _missing_media_ids([(mf.id, mf.path) for mf in batch if mf.id is not None]))
                offset += len(batch)
                if len(batch) < page:
                    break
            for media_id in missing_ids:
                await self._repo.delete_media_file(media_id)
                files_removed += 1

        # 删除不被 Metadata / Actor URL 引用的 Resource.
        if payload.remove_unreferenced_resources:
            live_urls, live_hashes = await _collect_live_resource_refs(self._repo)
            resources_removed = await self._resource_store.purge_unreferenced(live_urls, live_hashes)

        logger.info("cleanup completed", files_removed=files_removed, resources_removed=resources_removed)

        return TaskResult(True, result=CleanupResult(files_removed=files_removed, resources_removed=resources_removed))
