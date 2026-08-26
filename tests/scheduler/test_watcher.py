"""FileWatcher 和 WatcherService 测试"""

import shutil
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from watchdog.events import DirCreatedEvent, FileCreatedEvent, FileDeletedEvent, FileMovedEvent
from watchdog.observers.polling import PollingObserver

from amane.db.repository import Repository
from amane.enums import LibraryAutomation
from amane.events import EventBus
from amane.scheduler.service import WatcherService
from amane.scheduler.watcher import DEBOUNCE_SECONDS, FileWatcher, _Handler
from tests.helpers import await_for, wait_for


class TestHandler:
    """内部 _Handler 类的测试"""

    def test_matches_media_extensions(self):
        handler = _Handler(library_id=1, patterns=None)
        assert handler._matches(Path("/tmp/video.mp4")) is True
        assert handler._matches(Path("/tmp/video.mkv")) is True
        assert handler._matches(Path("/tmp/video.avi")) is True
        assert handler._matches(Path("/tmp/video.txt")) is False
        assert handler._matches(Path("/tmp/video.py")) is False

    def test_skips_trailer_pattern(self):
        handler = _Handler(library_id=1, patterns=None, skip_patterns=["(?i)trailer"])
        assert handler._matches(Path("/tmp/trailer.mp4")) is False
        assert handler._matches(Path("/tmp/video.mp4")) is True
        assert handler._matches(Path("/tmp/中文预告片.mp4")) is True  # (?i)trailer 不匹配预告

    def test_skips_custom_preview_name(self):
        handler = _Handler(library_id=1, patterns=None, skip_patterns=["预告"])
        assert handler._matches(Path("/tmp/中文预告片.mp4")) is False
        assert handler._matches(Path("/tmp/video.mp4")) is True

    def test_skips_any_blacklist_pattern(self):
        """多个跳过正则任一命中即忽略 (预告片 + 黑名单组合)."""
        handler = _Handler(library_id=1, patterns=None, skip_patterns=["广告", "(?i)ads"])
        assert handler._matches(Path("/tmp/新片广告.mp4")) is False
        assert handler._matches(Path("/tmp/ADS_01.mkv")) is False
        assert handler._matches(Path("/tmp/video.mp4")) is True

    def test_trash_dir_always_ignored(self):
        """.amane_trash 内路径恒不匹配 (即使文件名是正常影片)."""
        handler = _Handler(library_id=1, patterns=None)
        assert handler._matches(Path("/lib/.amane_trash/video.mp4")) is False
        assert handler._matches(Path("/lib/.amane_trash/sub/ad.mp4")) is False

    def test_move_into_trash_records_src_delete(self):
        """把文件移入 .amane_trash: dest 不匹配 → 记录 src 为删除 (记录清理/归档竞态安全)."""
        handler = _Handler(library_id=1, patterns=None)
        handler.on_moved(FileMovedEvent(src_path="/lib/incoming/ad.mp4", dest_path="/lib/.amane_trash/ad.mp4"))
        assert "/lib/incoming/ad.mp4" in handler._pending_deletes
        assert handler._pending_moves == {}

    def test_move_out_of_trash_triggers_found(self):
        """从 .amane_trash 恢复文件 (移出): dest 匹配 → 记录为移动 (重新入库)."""
        handler = _Handler(library_id=1, patterns=None)
        handler.on_moved(FileMovedEvent(src_path="/lib/.amane_trash/video.mp4", dest_path="/lib/video.mp4"))
        assert "/lib/video.mp4" in handler._pending_moves

    def test_matches_custom_patterns(self):
        handler = _Handler(library_id=1, patterns=["*.mp4", "*.mkv"])
        assert handler._matches(Path("/tmp/video.mp4")) is True
        assert handler._matches(Path("/tmp/video.mkv")) is True
        assert handler._matches(Path("/tmp/video.avi")) is False

    def test_debounce_not_ready_immediately(self):
        handler = _Handler(library_id=1, patterns=None)
        handler._handle("/tmp/video.mp4")
        # 不应立即就绪
        ready = handler.get_ready_files()
        assert ready == []

    def test_debounce_ready_after_timeout(self):
        handler = _Handler(library_id=1, patterns=None)
        handler._handle("/tmp/video.mp4")
        # 模拟时间流逝
        handler._pending["/tmp/video.mp4"] = time.time() - DEBOUNCE_SECONDS - 1
        ready = handler.get_ready_files()
        assert len(ready) == 1
        assert ready[0] == Path("/tmp/video.mp4")
        # 获取后应被清除
        assert handler.get_ready_files() == []

    def test_ignores_non_media_files(self):
        handler = _Handler(library_id=1, patterns=None)
        handler._handle("/tmp/readme.txt")
        # txt 未被添加到 pending 因为 _handle 通过 _matches 过滤
        assert handler._pending == {}
        assert handler.get_ready_files() == []

    def test_on_created_triggers_handle(self):
        handler = _Handler(library_id=1, patterns=None)

        handler.on_created(FileCreatedEvent(src_path="/tmp/video.mp4"))
        assert "/tmp/video.mp4" in handler._pending

    def test_on_created_ignores_directories(self):
        handler = _Handler(library_id=1, patterns=None)

        # DirCreatedEvent has is_directory=True
        handler.on_created(DirCreatedEvent(src_path="/tmp/somedir"))
        assert handler._pending == {}

    def test_on_moved_triggers_handle(self):
        handler = _Handler(library_id=1, patterns=None)

        handler.on_moved(FileMovedEvent(src_path="/tmp/old.mkv", dest_path="/tmp/movie.mkv"))
        assert "/tmp/movie.mkv" in handler._pending_moves

    def test_on_deleted_adds_to_pending_deletes(self):
        handler = _Handler(library_id=1, patterns=None)
        handler.on_deleted(FileDeletedEvent(src_path="/tmp/video.mp4"))
        assert "/tmp/video.mp4" in handler._pending_deletes

    def test_on_deleted_ignores_non_media(self):
        handler = _Handler(library_id=1, patterns=None)
        handler.on_deleted(FileDeletedEvent(src_path="/tmp/notes.txt"))
        assert handler._pending_deletes == {}

    def test_on_deleted_removes_from_pending_creates(self):
        """文件创建后立即删除: 从 pending 中移除, 添加到 pending_deletes"""
        handler = _Handler(library_id=1, patterns=None)
        handler.on_created(FileCreatedEvent(src_path="/tmp/video.mp4"))
        assert "/tmp/video.mp4" in handler._pending
        handler.on_deleted(FileDeletedEvent(src_path="/tmp/video.mp4"))
        assert "/tmp/video.mp4" not in handler._pending
        assert "/tmp/video.mp4" in handler._pending_deletes

    def test_get_ready_deletes_after_debounce(self):
        handler = _Handler(library_id=1, patterns=None)
        handler._pending_deletes["/tmp/video.mp4"] = time.time() - DEBOUNCE_SECONDS - 1
        ready = handler.get_ready_deletes()
        assert len(ready) == 1
        assert ready[0] == Path("/tmp/video.mp4")
        assert handler.get_ready_deletes() == []

    def test_on_moved_records_src_as_delete_target(self):
        """移动到非媒体路径: src 记录为删除"""
        handler = _Handler(library_id=1, patterns=None)
        handler.on_moved(FileMovedEvent(src_path="/tmp/video.mp4", dest_path="/tmp/video.txt"))
        assert "/tmp/video.mp4" in handler._pending_deletes
        assert handler._pending_moves == {}

    def test_get_ready_moves_after_debounce(self):
        handler = _Handler(library_id=1, patterns=None)
        handler._pending_moves["/tmp/new.mp4"] = ("/tmp/old.mp4", time.time() - DEBOUNCE_SECONDS - 1)
        ready = handler.get_ready_moves()
        assert len(ready) == 1
        assert ready[0] == (Path("/tmp/old.mp4"), Path("/tmp/new.mp4"))
        assert handler.get_ready_moves() == []


class TestFileWatcher:
    """FileWatcher 类的测试"""

    def test_watch_creates_observer(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(tmp_path), library_id=1)
        assert watcher._observer is not None
        assert len(watcher._handlers) == 1

    def test_watch_polling_mode(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None, use_polling=True)
        watcher.watch(str(tmp_path), library_id=1)
        assert isinstance(watcher._observer, PollingObserver)

    def test_start_stop_lifecycle(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running

    def test_check_debounced_calls_callback(self, tmp_path: Path):
        received = []
        watcher = FileWatcher(on_file_found=lambda p, _lib: received.append(p))
        watcher.watch(str(tmp_path), library_id=1)

        # 手动注入一个已过 debounce 的 pending 文件
        handler = watcher._handlers[0]
        test_file = str(tmp_path / "test.mp4")
        handler._pending[test_file] = time.time() - DEBOUNCE_SECONDS - 1

        ready = watcher.check_debounced()
        assert len(ready) == 1
        assert ready[0] == Path(test_file)
        assert len(received) == 1
        assert received[0] == Path(test_file)

    def test_multiple_watch_paths(self, tmp_path: Path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(dir1), library_id=1)
        watcher.watch(str(dir2), library_id=2, patterns=["*.mkv"])
        assert len(watcher._handlers) == 2

    @pytest.mark.parametrize("use_polling", [True, False])
    def test_real_file_detection(self, tmp_path: Path, use_polling: bool):
        """集成测试: 创建文件并验证 watcher 能检测到 (两种策略)"""
        received = []
        watcher = FileWatcher(on_file_found=lambda p, _lib: received.append(p), use_polling=use_polling)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.start()

        try:
            # 创建媒体文件
            test_file = tmp_path / "new_video.mp4"
            test_file.write_bytes(b"\x00" * 1024)

            # 文件应在 pending 中但尚未就绪 (debounce)
            handler = watcher._handlers[0]
            wait_for(lambda: str(test_file) in handler._pending)

            # 强制跳过 debounce
            handler._pending[str(test_file)] = time.time() - DEBOUNCE_SECONDS - 1
            ready = watcher.check_debounced()
            assert len(ready) == 1
            assert ready[0] == test_file
        finally:
            watcher.stop()

    def test_is_running_false_before_start(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(tmp_path), library_id=1)
        assert not watcher.is_running

    def test_unwatch_removes_handler(self, tmp_path: Path):
        dir1 = tmp_path / "d1"
        dir2 = tmp_path / "d2"
        dir1.mkdir()
        dir2.mkdir()
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(dir1), library_id=1)
        watcher.watch(str(dir2), library_id=2)
        assert len(watcher._handlers) == 2

        watcher.unwatch(1)
        assert [h.library_id for h in watcher._handlers] == [2]
        assert 1 not in watcher._watches

    def test_unwatch_unknown_is_noop(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.unwatch(999)  # 不应抛出
        assert len(watcher._handlers) == 1

    def test_unwatch_while_running(self, tmp_path: Path):
        watcher = FileWatcher(on_file_found=lambda p, _lib: None)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.start()
        try:
            watcher.unwatch(1)
            assert watcher._handlers == []
        finally:
            watcher.stop()


class TestWatcherService:
    """WatcherService 编排测试"""

    @pytest_asyncio.fixture
    async def repo(self):
        """创建使用内存 SQLite 的异步 Repository"""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        yield Repository(engine)
        await engine.dispose()

    @pytest.fixture
    def bus(self):
        return EventBus()

    @pytest.fixture
    def service(self, repo: Repository, bus):
        return WatcherService(repo, bus, use_polling=True)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_without_watch_paths(self, service):
        """无监控路径配置时 service 不应启动 watcher"""
        await service.start()
        assert not service.is_running

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_with_watch_paths(self, service, repo: Repository, tmp_path: Path):
        """存在监控路径时 service 应启动"""
        await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.SCRAPE)
        await service.start()
        assert service.is_running
        await service.stop()
        assert not service.is_running

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_found_broadcasts_event(self, service, repo: Repository, bus, tmp_path: Path):
        """验证文件发现时发出事件"""
        lib = await repo.create_library(name="t", path=str(tmp_path))
        assert lib.id is not None
        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"\x00" * 100)

        await service._on_file_found(test_file, lib.id)

        media = await repo.get_media_file_by_path(str(test_file))
        assert media is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_found_skips_duplicate(self, service, repo: Repository, bus: EventBus, tmp_path: Path):
        """验证重复文件不会重复广播"""
        lib = await repo.create_library(name="t", path=str(tmp_path))
        assert lib.id is not None
        test_file = tmp_path / "MIDV-123.mp4"
        test_file.write_bytes(b"\x00" * 100)

        await service._on_file_found(test_file, lib.id)
        await service._on_file_found(test_file, lib.id)

        tasks = await repo.list_tasks()
        assert len(tasks) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_found_watch_only_does_not_scrape(self, service, repo: Repository, tmp_path: Path) -> None:
        """automation=watch 只登记, 不入队 SCRAPE."""
        lib = await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.WATCH)
        assert lib.id is not None
        test_file = tmp_path / "MIDV-123.mp4"
        test_file.write_bytes(b"\x00" * 100)

        await service._on_file_found(test_file, lib.id)

        media = await repo.get_media_file_by_path(str(test_file))
        assert media is not None
        assert media.number == "MIDV-123"
        assert await repo.list_tasks() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_debounce_loop_flushes_ready_files(self, service, repo: Repository, bus, tmp_path: Path):
        """验证 debounce 循环处理通过 debounce 窗口的文件"""
        await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.SCRAPE)
        await service.start()

        # 注入一个虚假的 pending 路径 (磁盘上非真实文件, 因此
        # polling observer 不会重新检测并覆盖我们的时间戳)
        handler = service._watcher._handlers[0]
        fake_path = str(tmp_path / "phantom.mp4")
        handler._pending[fake_path] = time.time() - DEBOUNCE_SECONDS - 1

        # 给 debounce 循环时间来执行 + ensure_future 回调完成
        # debounce 循环间隔为 1s, 回调是 fire-and-forget, 需要额外的事件循环轮次
        media = await await_for(lambda: repo.get_media_file_by_path(fake_path))
        assert media is not None

        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_is_idempotent(self, service):
        """未运行时调用 stop 应是安全的"""
        await service.stop()  # 无操作, 不应抛出异常
        assert not service.is_running

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_is_idempotent(self, service, repo: Repository, tmp_path: Path):
        """调用两次 start 不应创建重复的 watcher"""
        await repo.create_library(name="t", path=str(tmp_path), automation=LibraryAutomation.SCRAPE)
        await service.start()
        await service.start()  # 第二次调用 - 应为无操作
        assert service.is_running
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_deleted_removes_from_db(self, service, repo: Repository, tmp_path: Path):
        """文件删除时从 DB 移除 MediaFile"""
        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"\x00" * 100)

        # 先注册文件
        media = await repo.create_media_file(library_id=1, path=str(test_file))
        assert media.id is not None

        # 调用删除处理器
        await service._on_file_deleted(test_file)

        # 验证已从 DB 移除
        assert await repo.get_media_file(media.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_deleted_ignores_untracked(self, service, repo: Repository, tmp_path: Path):
        """删除未追踪的文件不会报错"""
        test_file = tmp_path / "unknown.mp4"
        await service._on_file_deleted(test_file)  # 不应抛出

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_moved_updates_path(self, service, repo: Repository, tmp_path: Path):
        """文件移动时更新 DB 中的路径"""
        src = tmp_path / "old.mp4"
        dest = tmp_path / "new.mp4"
        src.write_bytes(b"\x00" * 100)

        media = await repo.create_media_file(library_id=1, path=str(src))
        assert media.id is not None

        await service._on_file_moved(src, dest, 1)

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.path == str(dest)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_on_file_moved_untracked_creates_new(self, service, repo: Repository, tmp_path: Path):
        """源文件不在 DB 中时, 视为新文件发现"""
        src = tmp_path / "unknown.mp4"
        dest = tmp_path / "MIDV-123.mp4"
        dest.write_bytes(b"\x00" * 100)

        await service._on_file_moved(src, dest, 1)

        media = await repo.get_media_file_by_path(str(dest))
        assert media is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_add_library_hot(self, service, repo: Repository, tmp_path: Path):
        """热添加监控库"""
        service.add_library(str(tmp_path), library_id=1)
        assert service.is_running
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_library_before_start_is_noop(self, service):
        """watcher 未启动时移除库应为无操作, 不抛出."""
        service.remove_library(1)
        assert not service.is_running

    @pytest.mark.asyncio(loop_scope="function")
    async def test_remove_library_hot(self, service, tmp_path: Path):
        """热移除监控库: 对应 handler 被取消监控."""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        service.add_library(str(d1), library_id=1)
        service.add_library(str(d2), library_id=2)
        assert {h.library_id for h in service._watcher._handlers} == {1, 2}

        service.remove_library(1)
        assert {h.library_id for h in service._watcher._handlers} == {2}
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_add_library_twice_replaces(self, service, tmp_path: Path):
        """重复 add_library 同一 id 不应产生重复 handler (幂等)."""
        service.add_library(str(tmp_path), library_id=1)
        service.add_library(str(tmp_path), library_id=1)
        assert [h.library_id for h in service._watcher._handlers] == [1]
        await service.stop()


class TestFileWatcherTmpFiles:
    """集成测试: 通过 tmp_path 产生真实文件系统事件, 覆盖两种 observer 策略"""

    @pytest.mark.parametrize("use_polling", [True, False])
    def test_created_file_enters_pending(self, tmp_path: Path, use_polling: bool):
        """在监控目录中创建媒体文件会将其加入 pending"""
        received = []
        watcher = FileWatcher(on_file_found=lambda p, _lib: received.append(p), use_polling=use_polling)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.start()

        try:
            video = tmp_path / "movie.mkv"
            video.write_bytes(b"\x00" * 512)

            handler = watcher._handlers[0]
            wait_for(lambda: str(video) in handler._pending)
        finally:
            watcher.stop()

    @pytest.mark.parametrize("use_polling", [True, False])
    def test_non_media_file_ignored(self, tmp_path: Path, use_polling: bool):
        """非媒体文件不会被 watcher 拾取"""
        watcher = FileWatcher(on_file_found=lambda p, _lib: None, use_polling=use_polling)
        watcher.watch(str(tmp_path), library_id=1)
        watcher.start()

        try:
            txt = tmp_path / "notes.txt"
            txt.write_text("hello")

            handler = watcher._handlers[0]
            wait_for(lambda: str(txt) not in handler._pending, duration=2)
        finally:
            watcher.stop()

    @pytest.mark.parametrize("use_polling", [True, False])
    def test_subdirectory_recursive(self, tmp_path: Path, use_polling: bool):
        """递归监控能检测到子目录中的文件"""
        subdir = tmp_path / "season1"
        subdir.mkdir()

        watcher = FileWatcher(on_file_found=lambda p, _lib: None, use_polling=use_polling)
        watcher.watch(str(tmp_path), library_id=1, recursive=True)
        watcher.start()

        try:
            video = subdir / "ep01.mp4"
            video.write_bytes(b"\x00" * 256)

            handler = watcher._handlers[0]
            wait_for(lambda: str(video) in handler._pending)
        finally:
            watcher.stop()

    @pytest.mark.parametrize("use_polling", [True, False])
    def test_moved_file_detected(self, tmp_path: Path, use_polling: bool):
        """将文件重命名/移动到监控目录会触发检测"""
        staging = tmp_path / "staging"
        staging.mkdir()
        watched = tmp_path / "watched"
        watched.mkdir()

        # 在监控目录外创建文件
        src = staging / "clip.mp4"
        src.write_bytes(b"\x00" * 128)

        watcher = FileWatcher(on_file_found=lambda p, _lib: None, use_polling=use_polling)
        watcher.watch(str(watched), library_id=1)
        watcher.start()

        try:
            # 移入监控目录 (同文件系统, shutil.move 使用 os.rename)
            dest = watched / "clip.mp4"
            shutil.move(str(src), str(dest))

            handler = watcher._handlers[0]
            # PollingObserver 通过 on_created 检测新文件;
            # 原生 observer 通过 on_moved 检测 (同文件系统 rename)
            wait_for(lambda: str(dest) in handler._pending)
        finally:
            watcher.stop()
