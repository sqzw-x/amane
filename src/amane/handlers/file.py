"""ORGANIZE 的文件后处理 (下载图到库路径, 移文件, 写 NFO)."""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings, WatermarkConfig
from ..enums import DownloadableResource, LinkMode
from ..media import ResourceStore, apply_cover_watermarks_from_info, crop_poster
from ..media import write_nfo as write_nfo_file
from ..media.pipeline import RESOURCE_URL_PREFIX
from ..net.http import WebClient
from ..organize import (
    MoveMode,
    ResolvedPaths,
    discover_subtitles,
    execute_organize,
    place_subtitles,
    render_strm_content,
    resolve_paths,
)
from ..organize.file import OrganizeResult as DiskOrganizeResult
from ..organize.link import create_video_link
from ..parsing import FileInfo, parse_file_info
from ..utils.extensions import MEDIA_EXTENSIONS, TRASH_DIRNAME, compile_skip_patterns, is_undersized_video
from ..utils.threads import in_thread, path_exists, path_is_dir
from ._common import aiter_media_files
from .models import CleanupPayload, CleanupResult, OrganizePayload, OrganizeResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import Library, MediaFile, Metadata
    from ..db.repository import Repository
    from ..media import ResourceStore
    from ..net.http import WebClient

logger = structlog.get_logger()


@dataclass
class FileOperationsResult:
    """file operations 执行结果."""

    success: bool
    dest: Path | None = None
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
) -> FileOperationsResult:
    """
    执行文件后处理: 图片下载, 文件整理, NFO 生成.

    Args:
        media_file: 要处理的 MediaFile 记录
        metadata: 聚合后的元数据 (AggregatedMetadata 或 db.Metadata)
        paths: 渲染后的输出路径 (由 resolve_paths 生成)
        move_mode: 文件操作模式 (move/copy/hardlink/symlink)
        resource_store: 资源缓存层 (图片下载经其多源容错, 由调用方强制注入)
        download_images: 是否下载图片
        write_nfo: 是否写入 NFO 文件
        copy_resources: 要复制到库路径的资源类型; None 表示全部
        web_client: HTTP 客户端 (可选, 无则跳过下载)
        config: 热配置 (仅用于读取 scraping 细节设置)
        library: 有则整理同目录字幕文件; None 跳过
        file_info: 源文件解析 (分集配对); library 非空且此项为空时从 source 现算
        safe_dirs: 字幕绝对路径模板允许落地的可信目录
        watermark_dir: 用户角标覆盖目录 (`{data_dir}/watermarks`); None 只用包内置

    Returns:
        FileOperationsResult 包含是否成功和目标路径
    """
    source_path = Path(media_file.path)
    if not await path_exists(source_path):
        logger.warning("source file missing", path=str(source_path))
        return FileOperationsResult(success=False, error=f"Source file not found: {media_file.path}")

    info = file_info if file_info is not None else parse_file_info(source_path)

    # 1. 下载图片到 paths 指定的位置 (水印打在库路径副本上, 不改 Resource 原图)
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

    # 2. 先发现同目录字幕 (视频挪走前), 再移动/复制视频
    subtitles: list[Path] = []
    if library is not None:
        subtitles = await discover_subtitles(source_path, library.subtitle_extensions, info.cd)

    org_result = await execute_organize(
        source=source_path,
        target_dir=paths.video.parent,
        target_stem=paths.video.stem,
        mode=move_mode,
    )

    # 3. 视频就位后写链接 (strm / 软链接); 失败仍带 dest 以便回写 MediaFile.path
    if org_result.success and org_result.dest and paths.link is not None:
        mode = LinkMode(library.link_mode) if library is not None else LinkMode.STRM
        strm_content: str | None = None
        if mode == LinkMode.STRM and library is not None:
            try:
                strm_content = render_strm_content(library.strm_content_template, org_result.dest, Path(library.path))
            except ValueError as e:
                return FileOperationsResult(success=False, dest=org_result.dest, error=str(e))
        link_result = await create_video_link(org_result.dest, paths.link, mode, content=strm_content)
        if not link_result.success:
            return FileOperationsResult(success=False, dest=org_result.dest, error=link_result.error)

    # 4. 写入 NFO (路径由库 nfo_template 渲染, 直接写渲染结果)
    if org_result.success and org_result.dest and write_nfo:
        await write_nfo_file(metadata, paths.nfo)

    if org_result.success and org_result.dest and library is not None and subtitles:
        await place_subtitles(
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
        )

    if org_result.success:
        logger.debug("file operations done", number=metadata.number, dest=str(org_result.dest), mode=str(move_mode))
        return FileOperationsResult(success=True, dest=org_result.dest)
    logger.debug("file operations failed", number=metadata.number, error=org_result.error)
    return FileOperationsResult(success=False, error=org_result.error)


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
    """ORGANIZE 的 file operations 编排.

    收敛"取 MediaFile → 取 Library → 渲染路径 → 执行 file operations".
    缺少 media_file_id / 对应记录不存在时, 返回 None 表示跳过 (不视为失败).
    Library 由 `MediaFile.library_id` 非空 FK 派生.

    Args:
        repo: 数据仓库
        media_file_id: 目标 MediaFile id, None 时跳过
        metadata: 已聚合/已有的元数据
        config: 热配置 (下载开关等)
        resource_store: 资源缓存层 (图片下载经其多源容错, 强制注入)
        write_nfo: 整理成功后是否写入 NFO
        copy_resources: 要复制到库路径的资源类型; None 表示全部
        web_client: HTTP 客户端
        safe_dirs: 绝对路径模板允许落地的可信目录集
        watermark_dir: 用户角标覆盖目录; None 只用包内置

    Returns:
        FileOperationsResult (含目标路径或错误); 前置条件不满足时返回 None
    """
    if media_file_id is None:
        return None
    media_file = await repo.get_media_file(media_file_id)
    if media_file is None:
        return None
    library = await repo.get_library(media_file.library_id)
    if library is None:
        return None

    ext = Path(media_file.path).suffix.lstrip(".")
    file_info = parse_file_info(media_file.path)
    paths = resolve_paths(
        library, metadata, ext=ext, file_info=file_info, source_path=Path(media_file.path), safe_dirs=safe_dirs
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
    )


async def _resolve_local(url: str, store: ResourceStore, client: WebClient) -> Path | None:
    """把 metadata 中的 URL 解析为本地文件路径.

    - 内部派生 URL (`/api/resources/{hash}`): 查 store 已存文件 (裁剪/超分产物).
    - 外部 URL: 经 store.acquire 下载 (命中缓存直出).
    """
    if url.startswith(RESOURCE_URL_PREFIX):
        url_hash = url.rsplit("/", 1)[-1]
        found = await store.get_by_url_hash(url_hash)
        return found[1] if found else None
    return await store.acquire(url, client)


async def _acquire_first_local(urls: list[str], store: ResourceStore, client: WebClient) -> Path | None:
    """逐 URL 解析到本地文件, 首个成功即返回 (多源容错)."""
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
    """把已 acquire 的本地资源复制到库路径并叠水印."""
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
    """把 metadata 引用的资源复制到库路径 (优先用 Resource 已有文件; 缺失则现场 acquire)."""
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
def _trash_if_unwanted(
    file_path: Path,
    trash_dir: Path,
    *,
    blacklisted: bool,
    is_trailer: bool,
    min_file_size: int,
    media_extensions: frozenset[str],
) -> DiskOrganizeResult | None:
    undersized = (not is_trailer) and is_undersized_video(file_path, min_file_size, media_extensions=media_extensions)
    if not blacklisted and not undersized:
        return None
    return execute_organize.sync(source=file_path, target_dir=trash_dir, target_stem=file_path.stem, mode=MoveMode.MOVE)


def _eligible_for_organize(
    file_path: Path,
    *,
    patterns: list[str] | None,
    skip_res: list[Pattern[str]] | None,
    min_file_size: int,
    media_extensions: frozenset[str],
) -> bool:
    """扫描结果中应执行路径模板落盘的路径.

    排除预告片、黑名单命中、小于 `min_file_size` 的视频, 以及不匹配 `patterns` 的路径.
    """
    if skip_res is not None and any(r.search(file_path.name) for r in skip_res):
        return False
    if patterns and not any(file_path.match(p) for p in patterns):
        return False
    return not is_undersized_video(file_path, min_file_size, media_extensions=media_extensions)


class OrganizeHandler(TaskHandler[OrganizePayload, OrganizeResult]):
    """依据已有 Metadata 将媒体文件整理至库路径; 不执行刮削, 不修改 Metadata.

    1. 删除磁盘上已不存在的 MediaFile 记录。名称冲突检测仅依据磁盘文件;
       失效 path 仍占用 UNIQUE 约束, 导致目标路径无法写入。
    2. 遍历目录中的媒体文件。遍历阶段不应用黑名单与 `min_file_size` 过滤,
       否则无法将命中文件归档至 `.amane_trash`。
    3. 将黑名单命中及小于 `min_file_size` 的视频移动至 `.amane_trash`;
       预告片保留原路径。
    4. 其余已关联 Metadata 的文件按路径模板落盘; 无 Metadata 则跳过。
    """

    def __init__(
        self,
        repo: Repository,
        config: HotSettings,
        resource_store: ResourceStore,
        web_client: WebClient | None = None,
        safe_dirs: Sequence[Path] | None = (),
        watermark_dir: Path | None = None,
    ):
        super().__init__(payload_t=OrganizePayload, result_t=OrganizeResult)
        self._repo = repo
        self._config = config
        self._web_client = web_client
        self._resource_store = resource_store
        self._safe_dirs = safe_dirs
        self._watermark_dir = watermark_dir

    async def handle(self, payload: OrganizePayload) -> TaskResult[OrganizeResult]:
        scan_dir = Path(payload.path)
        if not await path_is_dir(scan_dir):
            return TaskResult(success=False, error=f"Not a directory: {payload.path}")

        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None

        # 名称冲突检测仅依据磁盘上的 dest; 已删除文件的 path 若仍保留在库中, 会占用 UNIQUE 约束.
        indexed = await self._repo.list_media_files(library_id=library.id, limit=None)
        prune_total = len(indexed)
        if prune_total:
            await self.report_progress(0, prune_total, "prune")
            for i, mf in enumerate(indexed, start=1):
                if mf.id is not None and not await path_exists(Path(mf.path), follow_symlinks=False):
                    await self._repo.delete_media_file(mf.id)
                await self.report_progress(i, prune_total, "prune")

        recursive = payload.recursive if payload.recursive is not None else True
        media_extensions = frozenset(self._config.watcher.media_extensions) or MEDIA_EXTENSIONS

        organized = 0
        skipped = 0
        failed = 0

        await self.report_progress(0, 0, "scan")
        scanned = [
            p
            async for p in aiter_media_files(
                scan_dir,
                recursive=recursive,
                patterns=None,
                skip_patterns=None,
                media_extensions=media_extensions,
            )
        ]
        trashed, remaining = await self._trash_unwanted(library, scanned, media_extensions)
        skip_res = compile_skip_patterns([library.trailer_pattern, *(library.blacklist_patterns or [])])
        files = [
            p
            for p in remaining
            if _eligible_for_organize(
                p,
                patterns=payload.patterns,
                skip_res=skip_res,
                min_file_size=library.min_file_size,
                media_extensions=media_extensions,
            )
        ]
        total = len(files)
        if total == 0:
            await self.report_progress(1, 1, "done")
        else:
            await self.report_progress(0, total, "organize")
            for i, file_path in enumerate(files, start=1):
                path_str = str(file_path)

                media_file = await self._repo.get_media_file_by_path(path_str)
                if media_file is None or media_file.metadata_id is None:
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
                    await self._repo.update_media_file(media_file.id, path=str(fop_result.dest))
                if fop_result.success:
                    organized += 1
                else:
                    logger.warning("organize failed", path=path_str, error=fop_result.error)
                    failed += 1
                await self.report_progress(i, total, file_path.name)
            await self.report_progress(total, total, "done")

        logger.info(
            "organize completed",
            path=payload.path,
            organized=organized,
            skipped=skipped,
            failed=failed,
            trashed=trashed,
        )

        return TaskResult(
            True, result=OrganizeResult(organized=organized, skipped=skipped, failed=failed, trashed=trashed)
        )

    async def _trash_unwanted(
        self,
        library: Library,
        files: Sequence[Path],
        media_extensions: frozenset[str],
    ) -> tuple[int, list[Path]]:
        """将黑名单命中及小于 `min_file_size` 的视频移动至库根 `.amane_trash`.

        返回 `(成功归档数, 未归档路径)`. 预告片不归档. 图片、NFO、字幕、`.strm`
        不参与文件大小判定. 归档固定为物理移动, 不受 `move_mode` 影响. 失败仅记录日志,
        路径仍包含在返回列表中, 由后续落盘过滤排除.
        """
        blacklist = library.blacklist_patterns
        min_file_size = library.min_file_size
        if not blacklist and min_file_size <= 0:
            return 0, list(files)
        trash_res = compile_skip_patterns(blacklist)
        trailer_res = compile_skip_patterns([library.trailer_pattern])
        trash_dir = Path(library.path) / TRASH_DIRNAME
        trashed = 0
        remaining: list[Path] = []
        trash_total = len(files)
        if trash_total:
            await self.report_progress(0, trash_total, "trash")
        for i, file_path in enumerate(files, start=1):
            blacklisted = trash_res is not None and any(r.search(file_path.name) for r in trash_res)
            is_trailer = trailer_res is not None and any(r.search(file_path.name) for r in trailer_res)
            result = await _trash_if_unwanted(
                file_path,
                trash_dir,
                blacklisted=blacklisted,
                is_trailer=is_trailer,
                min_file_size=min_file_size,
                media_extensions=media_extensions,
            )
            if result is None:
                remaining.append(file_path)
                await self.report_progress(i, trash_total, "trash")
                continue
            if not result.success:
                logger.warning("unwanted file trash failed", path=str(file_path), error=result.error)
                remaining.append(file_path)
                await self.report_progress(i, trash_total, "trash")
                continue
            logger.info("unwanted file trashed", path=str(file_path), dest=str(result.dest))
            media_file = await self._repo.get_media_file_by_path(str(file_path))
            if media_file is not None:
                assert media_file.id is not None
                await self._repo.delete_media_file(media_file.id)
            trashed += 1
            await self.report_progress(i, trash_total, "trash")
        return trashed, remaining


def _add_resource_ref(url: str, live_urls: set[str], live_hashes: set[str]) -> None:
    """把 metadata 中的一个 URL 记入存活集合 (外部 locator 或内部 /api/resources/{hash})."""
    if not url:
        return
    prefix = f"{RESOURCE_URL_PREFIX}/"
    if url.startswith(prefix):
        live_hashes.add(url[len(prefix) :].split("?", 1)[0])
    else:
        live_urls.add(url)


async def _collect_live_resource_refs(repo: Repository) -> tuple[set[str], set[str]]:
    """扫 Metadata / Actor 媒体 URL 字段, 得到仍被引用的 Resource locator / url_hash."""
    live_urls: set[str] = set()
    live_hashes: set[str] = set()
    offset = 0
    page = 500
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
    """磁盘上不存在的 MediaFile id. 路径可能在 FUSE/NAS."""
    return [media_id for media_id, path in rows if not Path(path).exists(follow_symlinks=False)]


class CleanupHandler(TaskHandler[CleanupPayload, CleanupResult]):
    """处理 CLEANUP 任务 - 清理悬空引用.

    Metadata 是一等公民, 不因缺少 MediaFile 而被删除.
    流程:
        1. 磁盘上不存在的 MediaFile 索引 → 删记录 (可选)
        2. 不被任何 Metadata URL 字段引用的 Resource → 删文件+记录 (可选)
    """

    def __init__(self, repo: Repository, resource_store: ResourceStore):
        super().__init__(payload_t=CleanupPayload, result_t=CleanupResult)
        self._repo = repo
        self._resource_store = resource_store

    async def handle(self, payload: CleanupPayload) -> TaskResult[CleanupResult]:
        files_removed = 0
        resources_removed = 0

        if payload.remove_missing_files:
            missing_ids: list[int] = []
            offset = 0
            page = 500
            while True:
                batch = await self._repo.list_media_files(limit=page, offset=offset)
                if not batch:
                    break
                missing_ids.extend(
                    await _missing_media_ids(
                        [(mf.id, mf.path) for mf in batch if mf.id is not None],
                    )
                )
                offset += len(batch)
                if len(batch) < page:
                    break
            for media_id in missing_ids:
                await self._repo.delete_media_file(media_id)
                files_removed += 1

        if payload.remove_unreferenced_resources:
            live_urls, live_hashes = await _collect_live_resource_refs(self._repo)
            resources_removed = await self._resource_store.purge_unreferenced(live_urls, live_hashes)

        logger.info("cleanup completed", files_removed=files_removed, resources_removed=resources_removed)

        return TaskResult(True, result=CleanupResult(files_removed=files_removed, resources_removed=resources_removed))
