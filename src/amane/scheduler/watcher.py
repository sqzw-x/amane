"""带防抖与媒体扩展名过滤的目录监控."""

import time
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from ..utils.extensions import MEDIA_EXTENSIONS, compile_skip_patterns, is_in_trash

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from watchdog.observers.api import BaseObserver, ObservedWatch

_DEFAULT_DEBOUNCE_SECONDS = 3.0

# 测试中引用此名称, 保留常量别名.
DEBOUNCE_SECONDS = _DEFAULT_DEBOUNCE_SECONDS


class _Handler(FileSystemEventHandler):
    """内部 watchdog 事件处理器, 带防抖功能.

    每个 handler 绑定唯一的 library_id (一个监控根 = 一个 Library), 使文件事件
    天然携带归属信息, 无需事后按路径前缀反推.
    """

    def __init__(
        self,
        library_id: int,
        patterns: list[str] | None,
        media_extensions: frozenset[str] = MEDIA_EXTENSIONS,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        skip_patterns: Sequence[str | None] | None = None,
    ):
        super().__init__()
        self.library_id = library_id
        self._patterns = patterns
        self._media_extensions = media_extensions
        self._debounce_seconds = debounce_seconds
        self._skip_res = compile_skip_patterns(skip_patterns)
        self._pending: dict[str, float] = {}
        self._pending_deletes: dict[str, float] = {}
        self._pending_moves: dict[str, tuple[str, float]] = {}  # dest -> (src, timestamp)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(str(event.src_path))

    def on_deleted(self, event):
        if not event.is_directory:
            path_str = str(event.src_path)
            path = Path(path_str)
            if self._matches(path):
                # 从 pending 创建中移除 (尚未处理的创建事件)
                self._pending.pop(path_str, None)
                self._pending_deletes[path_str] = time.time()

    def on_moved(self, event):
        if not event.is_directory:
            src_str = str(event.src_path)
            dest_str = str(event.dest_path)
            dest = Path(dest_str)

            # 源路径视为删除
            self._pending.pop(src_str, None)
            self._pending_deletes.pop(src_str, None)

            if self._matches(dest):
                # 目标匹配: 记录为移动 (src -> dest)
                self._pending_moves[dest_str] = (src_str, time.time())
            else:
                # 目标不匹配: 等同于删除源文件
                src = Path(src_str)
                if self._matches(src):
                    self._pending_deletes[src_str] = time.time()

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        if self._matches(path):
            self._pending[path_str] = time.time()

    def _matches(self, path: Path) -> bool:
        if is_in_trash(path):
            return False
        if self._skip_res is not None and any(r.search(path.name) for r in self._skip_res):
            return False
        if self._patterns:
            return any(path.match(p) for p in self._patterns)
        return path.suffix.lower() in self._media_extensions

    def get_ready_files(self) -> list[Path]:
        """返回已稳定超过 debounce_seconds 的新文件"""
        now = time.time()
        ready = []
        remaining = {}
        for path_str, timestamp in self._pending.items():
            if now - timestamp >= self._debounce_seconds:
                ready.append(Path(path_str))
            else:
                remaining[path_str] = timestamp
        self._pending = remaining
        return ready

    def get_ready_deletes(self) -> list[Path]:
        """返回已稳定超过 debounce_seconds 的删除文件"""
        now = time.time()
        ready = []
        remaining = {}
        for path_str, timestamp in self._pending_deletes.items():
            if now - timestamp >= self._debounce_seconds:
                ready.append(Path(path_str))
            else:
                remaining[path_str] = timestamp
        self._pending_deletes = remaining
        return ready

    def get_ready_moves(self) -> list[tuple[Path, Path]]:
        """返回已稳定的移动事件 (src, dest) 列表"""
        now = time.time()
        ready = []
        remaining = {}
        for dest_str, (src_str, timestamp) in self._pending_moves.items():
            if now - timestamp >= self._debounce_seconds:
                ready.append((Path(src_str), Path(dest_str)))
            else:
                remaining[dest_str] = (src_str, timestamp)
        self._pending_moves = remaining
        return ready


class FileWatcher:
    """
    监控目录中的媒体文件变动 (新增, 删除, 移动), 带防抖功能.

    策略选择:
        use_polling=False (默认): 使用原生 OS 事件
           - Linux: inotify (不支持 NFS/CIFS 等网络挂载)
           - macOS: FSEvents
           - Windows: ReadDirectoryChangesW
        use_polling=True: 轮询模式, 适用于:
           - NAS/NFS 挂载
           - Docker Desktop for macOS (VirtioFS inotify 不可靠)
           - WSL2 (跨 OS 文件系统通知不完整)
           - inotify watch 数量超限

    用法:
        watcher = FileWatcher(on_file_found=my_callback, on_file_deleted=del_cb, on_file_moved=move_cb)
        watcher.watch("/media/videos", library_id=1, recursive=True)
        watcher.start()
        # 定期调用 watcher.check_debounced() 来刷新就绪文件
        watcher.stop()

    所有事件回调均额外接收来源 library_id (int), 指示事件来自哪个 Library.
    """

    def __init__(
        self,
        on_file_found: Callable[[Path, int], None],
        on_file_deleted: Callable[[Path, int], None] | None = None,
        on_file_moved: Callable[[Path, Path, int], None] | None = None,
        use_polling: bool = False,
        media_extensions: list[str] | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ):
        self._on_file_found = on_file_found
        self._on_file_deleted = on_file_deleted
        self._on_file_moved = on_file_moved
        self._use_polling = use_polling
        self._media_extensions = frozenset(media_extensions) if media_extensions else MEDIA_EXTENSIONS
        self._debounce_seconds = debounce_seconds
        self._observer: BaseObserver | None = None
        self._handlers: list[_Handler] = []
        self._watching: list[tuple[str, bool, list[str] | None]] = []
        # library_id -> 已注册的 watch 句柄, 供 unwatch 取消监控
        self._watches: dict[int, ObservedWatch] = {}

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def watch(
        self,
        path: str,
        library_id: int,
        recursive: bool = True,
        patterns: list[str] | None = None,
        skip_patterns: Sequence[str | None] | None = None,
    ) -> None:
        """
        添加一个要监控的目录.

        Args:
            path: 要监控的目录路径.
            library_id: 该目录所属的 Library id; 事件回调会携带此值.
            recursive: 是否监控子目录.
            patterns: 可选的 glob 模式 (例如 ["*.mp4", "*.mkv"]).
                     如果为 None, 则使用 MEDIA_EXTENSIONS.
            skip_patterns: 跳过正则列表 (预告片/黑名单), 命中文件名则忽略;
                          `.amane_trash` 目录 (回收站) 内路径恒忽略.
        """
        handler = _Handler(
            library_id,
            patterns,
            media_extensions=self._media_extensions,
            debounce_seconds=self._debounce_seconds,
            skip_patterns=skip_patterns,
        )
        self._handlers.append(handler)
        self._watching.append((path, recursive, patterns))
        if self._observer is None:
            observer_class = PollingObserver if self._use_polling else Observer
            self._observer = observer_class()
        self._watches[library_id] = self._observer.schedule(handler, path, recursive=recursive)

    def unwatch(self, library_id: int) -> None:
        """取消监控某个 Library 对应的目录 (运行时热移除, 无需重启).

        若该 library_id 未在监控中则为无操作.
        """
        watch = self._watches.pop(library_id, None)
        if watch is not None and self._observer is not None:
            self._observer.unschedule(watch)
        self._handlers = [h for h in self._handlers if h.library_id != library_id]

    def start(self) -> None:
        """启动文件监控器"""
        if self._observer:
            self._observer.start()

    def stop(self) -> None:
        """停止文件监控器并等待线程结束"""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None

    def check_debounced(self) -> list[Path]:
        """
        检查所有 handler 中已过防抖窗口且准备好处理的事件.

        返回新就绪的文件列表. 同时为每个事件调用对应回调 (携带 handler 的 library_id).
        """
        ready_files = []
        for handler in self._handlers:
            for path in handler.get_ready_files():
                self._on_file_found(path, handler.library_id)
                ready_files.append(path)
            if self._on_file_deleted:
                for path in handler.get_ready_deletes():
                    self._on_file_deleted(path, handler.library_id)
            if self._on_file_moved:
                for src, dest in handler.get_ready_moves():
                    self._on_file_moved(src, dest, handler.library_id)
        return ready_files
