import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from ..library import MEDIA_EXTENSIONS, LibraryFileKind, LibraryScan
from ..library.rules import is_in_trash
from ..utils.path import path_is_under

if TYPE_CHECKING:
    from collections.abc import Callable

    from watchdog.observers.api import BaseObserver, ObservedWatch

_DEFAULT_DEBOUNCE_SECONDS = 3.0
_DEFAULT_OBSERVER_TIMEOUT = 1.0  # 与 watchdog Observer/PollingObserver 构造默认一致

# 测试中引用此名称, 保留常量别名.
DEBOUNCE_SECONDS = _DEFAULT_DEBOUNCE_SECONDS


class _Handler(FileSystemEventHandler):
    """每个 handler 对应一个 library_id, 事件自带归属, 无需按路径前缀反推."""

    def __init__(
        self,
        library_id: int,
        scan: LibraryScan | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ):
        super().__init__()
        self.library_id = library_id
        self._scan = scan if scan is not None else LibraryScan()
        self._debounce_seconds = debounce_seconds
        self._pending: dict[str, float] = {}
        self._pending_deletes: dict[str, float] = {}
        self._pending_dir_deletes: dict[str, float] = {}
        self._pending_moves: dict[str, tuple[str, float]] = {}  # dest -> (src, timestamp)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(str(event.src_path))

    def on_deleted(self, event):
        # 前缀含路径自身: 文件删除只命中这一条; 目录删除命中子树.
        # Windows 删除通知不区分文件与目录, 一律按前缀处理.
        self._record_prefix_delete(str(event.src_path))

    def on_moved(self, event):
        if not event.is_directory:
            src_str = str(event.src_path)
            dest_str = str(event.dest_path)
            dest = Path(dest_str)

            # 源路径上的创建/删除待处理一律丢弃
            self._pending.pop(src_str, None)
            self._pending_deletes.pop(src_str, None)

            if self._matches(dest):
                # 目标匹配: 记录为移动
                self._pending_moves[dest_str] = (src_str, time.time())
            else:
                # 目标不匹配: 源若为媒体则记录为删除
                src = Path(src_str)
                if self._matches(src):
                    self._pending_deletes[src_str] = time.time()

    def _record_prefix_delete(self, dir_str: str) -> None:
        """按路径前缀清未提交事件; 防抖后由服务端按索引前缀删除."""
        if is_in_trash(Path(dir_str)):
            return
        # 已有更外层路径待删除时不必再记; 同一路径则刷新时间戳.
        if any(
            path_is_under(dir_str, existing) and not path_is_under(existing, dir_str)
            for existing in self._pending_dir_deletes
        ):
            self._drop_pending_under(dir_str)
            return
        self._drop_pending_under(dir_str)
        self._pending_dir_deletes[dir_str] = time.time()

    def _drop_pending_under(self, root: str) -> None:
        self._pending = {path: ts for path, ts in self._pending.items() if not path_is_under(path, root)}
        self._pending_deletes = {
            path: ts for path, ts in self._pending_deletes.items() if not path_is_under(path, root)
        }
        self._pending_moves = {
            dest: src_ts
            for dest, src_ts in self._pending_moves.items()
            if not path_is_under(dest, root) and not path_is_under(src_ts[0], root)
        }
        self._pending_dir_deletes = {
            path: ts for path, ts in self._pending_dir_deletes.items() if not path_is_under(path, root)
        }

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        if self._matches(path):
            self._pending[path_str] = time.time()

    def _matches(self, path: Path) -> bool:
        return self._scan.classify(path) is LibraryFileKind.MEDIA

    def get_ready_files(self) -> list[Path]:
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

    def get_ready_dir_deletes(self) -> list[Path]:
        now = time.time()
        ready = []
        remaining = {}
        for path_str, timestamp in self._pending_dir_deletes.items():
            if now - timestamp >= self._debounce_seconds:
                ready.append(Path(path_str))
            else:
                remaining[path_str] = timestamp
        self._pending_dir_deletes = remaining
        return ready

    def get_ready_moves(self) -> list[tuple[Path, Path]]:
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
    """媒体目录监控, 带防抖. 回调额外接收 library_id.

    use_polling=False 使用原生 OS 事件: Linux inotify 不支持 NFS/CIFS 等网络挂载.
    use_polling=True 适用于 NAS/NFS、Docker Desktop for macOS (VirtioFS inotify
    不可靠)、WSL2, 以及 inotify watch 数量超限.
    """

    def __init__(
        self,
        on_file_found: Callable[[Path, int], None],
        on_file_deleted: Callable[[Path, int], None] | None = None,
        on_dir_deleted: Callable[[Path, int], None] | None = None,
        on_file_moved: Callable[[Path, Path, int], None] | None = None,
        use_polling: bool = False,
        media_extensions: list[str] | None = None,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        observer_timeout: float = _DEFAULT_OBSERVER_TIMEOUT,
    ):
        self._on_file_found = on_file_found
        self._on_file_deleted = on_file_deleted
        self._on_dir_deleted = on_dir_deleted
        self._on_file_moved = on_file_moved
        self._use_polling = use_polling
        self._media_extensions = frozenset(media_extensions) if media_extensions else MEDIA_EXTENSIONS
        self._debounce_seconds = debounce_seconds
        self._observer_timeout = observer_timeout
        self._observer: BaseObserver | None = None
        self._handlers: list[_Handler] = []
        self._watching: list[tuple[str, bool, list[str] | None]] = []
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
        min_file_size: int = 0,
    ) -> None:
        """登记监控目录.

        skip_patterns 命中则不登记; `.amane_trash` 内路径恒忽略.
        min_file_size 只对 media_extensions 判定, `.strm` 指针不参与.
        """
        handler = _Handler(
            library_id,
            scan=LibraryScan(
                patterns=patterns,
                blacklist_patterns=[p for p in skip_patterns if p] if skip_patterns else None,
                min_file_size=min_file_size,
                media_extensions=self._media_extensions,
            ),
            debounce_seconds=self._debounce_seconds,
        )
        self._handlers.append(handler)
        self._watching.append((path, recursive, patterns))
        if self._observer is None:
            observer_class = PollingObserver if self._use_polling else Observer
            self._observer = observer_class(timeout=self._observer_timeout)
        self._watches[library_id] = self._observer.schedule(handler, path, recursive=recursive)

    def unwatch(self, library_id: int) -> None:
        """取消该 library_id 的监控; 未在监控中则为无操作."""
        watch = self._watches.pop(library_id, None)
        if watch is not None and self._observer is not None:
            self._observer.unschedule(watch)
        self._handlers = [h for h in self._handlers if h.library_id != library_id]

    def start(self) -> None:
        if self._observer:
            self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            if self._observer.is_alive():
                self._observer.join(timeout=5.0)
            self._observer = None

    def check_debounced(self) -> list[Path]:
        """刷新已过防抖窗口的事件并调用对应回调."""
        ready_files = []
        for handler in self._handlers:
            # 目录前缀删除须先于创建: 同窗口内重建的文件应在清索引之后重新登记.
            if self._on_dir_deleted:
                for path in handler.get_ready_dir_deletes():
                    self._on_dir_deleted(path, handler.library_id)
            if self._on_file_deleted:
                for path in handler.get_ready_deletes():
                    self._on_file_deleted(path, handler.library_id)
            if self._on_file_moved:
                for src, dest in handler.get_ready_moves():
                    self._on_file_moved(src, dest, handler.library_id)
            for path in handler.get_ready_files():
                self._on_file_found(path, handler.library_id)
                ready_files.append(path)
        return ready_files
