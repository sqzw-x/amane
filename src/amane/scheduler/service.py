"""编排 FileWatcher → Repository / EventBus."""

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from ..db.models import TaskType
from ..enums import LibraryAutomation
from ..events import EventBus, EventType
from ..handlers._common import register_media_file
from ..handlers.models import ScrapePayload
from ..parsing import parse_file_info
from .watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..db.repository import Repository

logger = structlog.get_logger()

# 默认防抖检查间隔 (秒)
_CHECK_INTERVAL = 1.0


class WatcherService:
    """
    编排文件监控, 任务提交和事件广播.

    生命周期:
        service = WatcherService(repo, event_bus)
        await service.start()   # 从 DB 加载 watch_paths, 启动监控器
        ...
        await service.stop()    # 停止监控器和后台循环

    事件处理:
       - 新文件: 创建 MediaFile; 库 automation=scrape 时解析番号并提交 SCRAPE
       - 文件删除: 从 DB 移除 MediaFile
       - 文件移动: 更新 MediaFile 路径
    """

    def __init__(
        self,
        repo: Repository,
        event_bus: EventBus,
        use_polling: bool = False,
        media_extensions: list[str] | None = None,
        debounce_seconds: float = 3.0,
        check_interval: float = _CHECK_INTERVAL,
        observer_timeout: float = 1.0,
    ):
        self._repo = repo
        self._event_bus = event_bus
        self._use_polling = use_polling
        self._media_extensions = media_extensions
        self._debounce_seconds = debounce_seconds
        self._check_interval = check_interval
        self._observer_timeout = observer_timeout
        self._watcher: FileWatcher | None = None
        self._debounce_task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _new_watcher(self) -> FileWatcher:
        return FileWatcher(
            on_file_found=self._on_file_found_sync,
            on_file_deleted=self._on_file_deleted_sync,
            on_file_moved=self._on_file_moved_sync,
            use_polling=self._use_polling,
            media_extensions=self._media_extensions,
            debounce_seconds=self._debounce_seconds,
            observer_timeout=self._observer_timeout,
        )

    async def start(self) -> None:
        """从 DB 加载监控路径并启动文件监控"""
        if self._running:
            return

        libraries = await self._repo.list_libraries(watch_only=True)
        if not libraries:
            logger.info("no watch-enabled libraries configured, watcher not started")
            return

        self._watcher = self._new_watcher()

        for lib in libraries:
            assert lib.id is not None
            logger.info("watching library", library_id=lib.id, path=lib.path, recursive=lib.recursive)
            self._watcher.watch(
                lib.path,
                library_id=lib.id,
                recursive=lib.recursive,
                patterns=lib.patterns,
                skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                min_file_size=lib.min_file_size,
            )

        try:
            self._watcher.start()
        except OSError as exc:
            if "inotify" in str(exc).lower() or "watch" in str(exc).lower():
                logger.error(
                    "Failed to start file watcher (inotify watch limit may be exceeded). "
                    "Try increasing /proc/sys/fs/inotify/max_user_watches or set watcher.use_polling = true in config."
                )
            raise
        self._running = True

        # 启动后台防抖检查器
        self._debounce_task = asyncio.create_task(self._debounce_loop())
        logger.info("watcher service started", library_count=len(libraries))

    async def stop(self) -> None:
        """停止文件监控和后台任务"""
        self._running = False
        if self._debounce_task:
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
            self._debounce_task = None

        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        logger.info("watcher service stopped")

    def add_library(
        self,
        path: str,
        library_id: int,
        recursive: bool = True,
        patterns: list[str] | None = None,
        skip_patterns: Sequence[str | None] | None = None,
        min_file_size: int = 0,
    ) -> None:
        """热添加监控库 (运行时调用, 无需重启)"""
        if self._watcher is None:
            # 首次添加: 创建 watcher 并启动
            self._watcher = self._new_watcher()
            self._watcher.watch(
                path,
                library_id=library_id,
                recursive=recursive,
                patterns=patterns,
                skip_patterns=skip_patterns,
                min_file_size=min_file_size,
            )
            self._watcher.start()
            self._running = True
            if self._debounce_task is None:
                self._debounce_task = asyncio.create_task(self._debounce_loop())
            logger.info("watcher service started for new library", library_id=library_id, path=path)
        else:
            self._watcher.unwatch(library_id)  # 幂等: 若已存在则先移除再添加
            self._watcher.watch(
                path,
                library_id=library_id,
                recursive=recursive,
                patterns=patterns,
                skip_patterns=skip_patterns,
                min_file_size=min_file_size,
            )
            logger.info("library watch added", library_id=library_id, path=path, recursive=recursive)

    def remove_library(self, library_id: int) -> None:
        """热移除监控库 (库被删除或禁用监控时调用, 无需重启).

        若 watcher 尚未启动或该库未在监控中则为无操作.
        """
        if self._watcher is None:
            return
        self._watcher.unwatch(library_id)
        logger.info("library watch removed", library_id=library_id)

    # --- 同步回调 (watchdog 线程 -> 事件循环) ---

    def _on_file_found_sync(self, path: Path, library_id: int) -> None:
        self._schedule_async(self._on_file_found(path, library_id))

    def _on_file_deleted_sync(self, path: Path, library_id: int) -> None:
        self._schedule_async(self._on_file_deleted(path))

    def _on_file_moved_sync(self, src: Path, dest: Path, library_id: int) -> None:
        self._schedule_async(self._on_file_moved(src, dest, library_id))

    def _schedule_async(self, coro) -> None:
        """将协程调度到事件循环"""
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(asyncio.ensure_future, coro)
        except RuntimeError:
            logger.warning("no event loop available")

    # --- 异步处理器 ---

    async def _on_file_found(self, path: Path, library_id: int) -> None:
        """新发现文件: 注册 MediaFile (归属 library_id); scrape 级别再解析番号并提交 SCRAPE"""
        path_str = str(path)

        existing = await self._repo.get_media_file_by_path(path_str)
        if existing is not None:
            logger.debug("file already tracked", path=path_str)
            return

        media = await register_media_file(self._repo, library_id, path)
        assert media.id is not None
        logger.info("file discovered", path=path_str, media_file_id=media.id, library_id=library_id)

        try:
            parsed = parse_file_info(path_str)
        except Exception:
            logger.debug("cannot parse number", path=path_str)
            parsed = None

        library = await self._repo.get_library(library_id)
        if parsed:
            await self._repo.update_media_file(media.id, number=parsed.number)
            if library is not None and library.automation == LibraryAutomation.SCRAPE:
                task = await self._repo.create_task(
                    task_type=TaskType.SCRAPE,
                    payload=ScrapePayload(
                        media_file_id=media.id, number=parsed.number, content_type=parsed.content_type
                    ),
                )
                logger.info("scrape task submitted", task_id=task.id, number=parsed.number, path=path_str)

        await self._event_bus.emit(EventType.FILE_DISCOVERED, {"path": path_str, "media_file_id": media.id})

    async def _on_file_deleted(self, path: Path) -> None:
        """文件被删除: 从 DB 移除 MediaFile"""
        path_str = str(path)
        media = await self._repo.get_media_file_by_path(path_str)
        if media is None:
            return

        assert media.id is not None
        await self._repo.delete_media_file(media.id)
        logger.info("file removed from db", path=path_str, media_file_id=media.id)

        await self._event_bus.emit(EventType.FILE_REMOVED, {"path": path_str, "media_file_id": media.id})

    async def _on_file_moved(self, src: Path, dest: Path, library_id: int) -> None:
        """文件被移动/重命名: 更新 MediaFile 路径"""
        src_str = str(src)
        dest_str = str(dest)

        media = await self._repo.get_media_file_by_path(src_str)
        if media is None:
            # 源文件不在 DB 中, 视为新文件 (归属 dest 所在 library)
            await self._on_file_found(dest, library_id)
            return

        assert media.id is not None
        await self._repo.update_media_file(media.id, path=dest_str)
        logger.info("file path updated", src=src_str, dest=dest_str, media_file_id=media.id)

    async def _debounce_loop(self) -> None:
        """定期检查防抖完成后可以处理的文件"""
        while self._running:
            await asyncio.sleep(self._check_interval)
            if self._watcher:
                self._watcher.check_debounced()
