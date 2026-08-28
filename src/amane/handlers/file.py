"""ORGANIZE 的文件后处理 (下载图到库路径, 移文件, 写 NFO)."""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings
from ..enums import DownloadableResource, LinkMode
from ..media import ResourceStore, crop_poster
from ..media import write_nfo as write_nfo_file
from ..media.pipeline import RESOURCE_URL_PREFIX
from ..net.http import WebClient
from ..organize import MoveMode, ResolvedPaths, discover_subtitles, execute_organize, place_subtitles, resolve_paths
from ..organize.link import create_video_link
from ..parsing import FileInfo, parse_file_info
from ..utils.extensions import TRASH_DIRNAME, compile_skip_patterns
from ._common import iter_media_files
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
    safe_dirs: Sequence[Path] = (),
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

    Returns:
        FileOperationsResult 包含是否成功和目标路径
    """
    source_path = Path(media_file.path)
    if not source_path.exists():
        logger.warning("source file missing", path=str(source_path))
        return FileOperationsResult(success=False, error=f"Source file not found: {media_file.path}")

    # 1. 下载图片到 paths 指定的位置
    if web_client and download_images:
        image_dir = paths.thumb.parent
        image_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("downloading images via store", number=metadata.number)
        kinds = set(copy_resources) if copy_resources is not None else set(DownloadableResource)
        await _download_images_via_store(metadata, resource_store, web_client, paths, config, kinds)

    # 2. 先发现同目录字幕 (视频挪走前), 再移动/复制视频
    info = file_info if file_info is not None else parse_file_info(source_path)
    subtitles: list[Path] = []
    if library is not None:
        subtitles = discover_subtitles(source_path, library.subtitle_extensions, info.cd)

    target_dir = paths.video.parent
    target_stem = paths.video.stem
    org_result = execute_organize(
        source=source_path,
        target_dir=target_dir,
        target_stem=target_stem,
        mode=move_mode,
    )

    # 3. 视频就位后写链接 (strm / 软链接); 失败仍带 dest 以便回写 MediaFile.path
    if org_result.success and org_result.dest and paths.link is not None:
        mode = LinkMode(library.link_mode) if library is not None else LinkMode.STRM
        link_result = create_video_link(org_result.dest, paths.link, mode)
        if not link_result.success:
            return FileOperationsResult(success=False, dest=org_result.dest, error=link_result.error)

    # 4. 写入 NFO (路径由库 nfo_template 渲染, 直接写渲染结果)
    if org_result.success and org_result.dest and write_nfo:
        await write_nfo_file(metadata, paths.nfo)

    if org_result.success and org_result.dest and library is not None and subtitles:
        place_subtitles(
            subtitles,
            video_source=source_path,
            video_dest=org_result.dest,
            library=library,
            metadata=metadata,
            file_info=info,
            mode=move_mode,
            safe_dirs=safe_dirs,
            link_dir=paths.link.parent if paths.link is not None else None,
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
    safe_dirs: Sequence[Path] = (),
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


async def _download_images_via_store(
    metadata: Metadata,
    store: ResourceStore,
    client: WebClient,
    paths: ResolvedPaths,
    config: HotSettings | None,
    kinds: set[DownloadableResource],
) -> None:
    """把 metadata 引用的资源复制到库路径 (优先用 Resource 已有文件; 缺失则现场 acquire)."""
    thumb_local = None
    if DownloadableResource.thumb in kinds:
        thumb_urls = metadata.thumb_urls
        thumb_local = await _acquire_first_local(thumb_urls, store, client) if thumb_urls else None
        if thumb_local:
            paths.thumb.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(thumb_local, paths.thumb)
            # Fanart = thumb (JAV convention)
            paths.fanart.parent.mkdir(parents=True, exist_ok=True)
            if not paths.fanart.exists():
                shutil.copy2(thumb_local, paths.fanart)

    if DownloadableResource.poster in kinds:
        poster_urls = metadata.poster_urls
        poster_local = await _acquire_first_local(poster_urls, store, client) if poster_urls else None
        if poster_local:
            paths.poster.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(poster_local, paths.poster)
        elif thumb_local and config and config.scraping.crop_poster:
            paths.poster.parent.mkdir(parents=True, exist_ok=True)
            crop_poster(
                paths.thumb,
                paths.poster,
                poster_ratio=config.scraping.poster_ratio,
                jpeg_quality=config.scraping.jpeg_quality,
            )

    if DownloadableResource.trailer in kinds:
        trailer_urls = metadata.trailer_urls
        trailer_local = await _acquire_first_local(trailer_urls, store, client) if trailer_urls else None
        if trailer_local:
            paths.trailer.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(trailer_local, paths.trailer)

    if DownloadableResource.extrafanart in kinds:
        extrafanart_by_site = metadata.extrafanart_urls
        if extrafanart_by_site:
            priority = list(extrafanart_by_site.keys())
            downloaded = await store.acquire_extrafanart(extrafanart_by_site, priority, client)
            if downloaded:
                paths.extrafanart_dir.mkdir(parents=True, exist_ok=True)
                for i, p in enumerate(downloaded):
                    shutil.copy2(p, paths.extrafanart_dir / f"{i + 1}.jpg")


class OrganizeHandler(TaskHandler[OrganizePayload, OrganizeResult]):
    """
    处理 ORGANIZE 任务 - 按已有元数据整理目录中的文件.

    流程:
        1. 清掉本库失效索引 (避免幽灵行占用 dest 碰撞名)
        2. 黑名单预处理: 命中库 blacklist_patterns 的文件移入库根 `.amane_trash` 并删记录
        3. 遍历目录, 过滤媒体文件 (预告片/黑名单命中跳过)
        4. 对每个文件: 查 MediaFile → 查关联 Metadata
        5. 有元数据: 执行 file operations
        6. 无元数据: 跳过

    与 SCRAPE 的区别: 数据源是本地 DB, 不联网、不改 Metadata.
    """

    def __init__(
        self,
        repo: Repository,
        config: HotSettings,
        resource_store: ResourceStore,
        web_client: WebClient | None = None,
        safe_dirs: Sequence[Path] = (),
    ):
        super().__init__(payload_t=OrganizePayload, result_t=OrganizeResult)
        self._repo = repo
        self._config = config
        self._web_client = web_client
        self._resource_store = resource_store
        self._safe_dirs = safe_dirs

    async def handle(self, payload: OrganizePayload) -> TaskResult[OrganizeResult]:
        scan_dir = Path(payload.path)
        if not scan_dir.is_dir():
            return TaskResult(success=False, error=f"Not a directory: {payload.path}")

        # 获取 Library 用于路径模板
        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None

        # 落盘前清掉本库失效索引, 避免 dest 碰撞名被幽灵行占用而撞 path UNIQUE.
        for mf in await self._repo.list_media_files(library_id=library.id, limit=None):
            if mf.id is not None and not Path(mf.path).exists(follow_symlinks=False):
                await self._repo.delete_media_file(mf.id)

        recursive = payload.recursive if payload.recursive is not None else True

        # 黑名单预处理: 先于主循环, 命中文件移入回收站后主循环不再触碰.
        trashed = await self._trash_blacklisted(library, scan_dir, recursive)

        organized = 0
        skipped = 0
        failed = 0

        for file_path in iter_media_files(
            scan_dir,
            recursive=recursive,
            patterns=payload.patterns,
            skip_patterns=[library.trailer_pattern, *(library.blacklist_patterns or [])],
        ):
            path_str = str(file_path)

            # 查找 MediaFile 记录
            media_file = await self._repo.get_media_file_by_path(path_str)
            if media_file is None or media_file.metadata_id is None:
                skipped += 1
                continue

            # 查找关联的 Metadata
            metadata = await self._repo.get_metadata(media_file.metadata_id)
            if metadata is None:
                skipped += 1
                continue

            # 执行 file operations
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
            )
            if fop_result is None:
                skipped += 1
                continue

            if fop_result.dest and media_file.id is not None:
                await self._repo.update_media_file(media_file.id, path=str(fop_result.dest))
            if fop_result.success:
                organized += 1
            else:
                logger.warning("organize failed", path=path_str, error=fop_result.error)
                failed += 1

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

    async def _trash_blacklisted(self, library: Library, scan_dir: Path, recursive: bool) -> int:
        """把扫描目录中命中库黑名单的媒体文件移入库根 `.amane_trash` (固定保留名).

        - 命中黑名单即判定非正片: 无论是否已有 MediaFile 记录都归档, 归档后删除记录.
        - 归档恒为物理移动, 不受库 move_mode 影响; 已归档的 `.amane_trash` 内容不再被遍历.
        - 失败只记日志, 不阻断整理; 返回成功归档数.
        """
        blacklist = library.blacklist_patterns
        if not blacklist:
            return 0
        trash_res = compile_skip_patterns(blacklist)
        if trash_res is None:
            return 0
        trash_dir = Path(library.path) / TRASH_DIRNAME
        trashed = 0
        for file_path in iter_media_files(scan_dir, recursive=recursive, patterns=None, skip_patterns=None):
            if not any(r.search(file_path.name) for r in trash_res):
                continue
            result = execute_organize(
                source=file_path, target_dir=trash_dir, target_stem=file_path.stem, mode=MoveMode.MOVE
            )
            if not result.success:
                logger.warning("blacklisted file trash failed", path=str(file_path), error=result.error)
                continue
            logger.info("blacklisted file trashed", path=str(file_path), dest=str(result.dest))
            media_file = await self._repo.get_media_file_by_path(str(file_path))
            if media_file is not None:
                assert media_file.id is not None
                await self._repo.delete_media_file(media_file.id)
            trashed += 1
        return trashed


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
                    mf.id for mf in batch if mf.id is not None and not Path(mf.path).exists(follow_symlinks=False)
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
