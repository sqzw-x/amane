"""CloudDrive webhook 分流与入库."""

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from amane.db.repository import Repository
from amane.enums import LibraryAutomation, LibraryIngest
from amane.events import EventBus
from amane.library import LibraryScan
from amane.library.cloud_path import normalize_cloud_path
from amane.scheduler.clouddrive import CloudDriveChange, CloudDriveRoute, match_route
from amane.scheduler.service import WatcherService
from amane.scheduler.watcher import FileWatcher


class TestMatchRoute:
    def test_longest_prefix_wins(self) -> None:
        scan = LibraryScan()
        wide = CloudDriveRoute(1, "/115open", "/vol/115", True, scan=scan)
        nested = CloudDriveRoute(2, "/115open/lib", "/vol/115/lib", True, scan=scan)
        hit = match_route("/115open/lib/a.mp4", [wide, nested])
        assert hit is not None
        assert hit.library_id == 2

    def test_unmatched_and_illegal(self) -> None:
        route = CloudDriveRoute(1, "/115open/lib", "/vol", True, scan=LibraryScan())
        assert match_route("/other/a.mp4", [route]) is None
        assert match_route("", [route]) is None
        assert normalize_cloud_path("/115open/lib/a.mp4").startswith("/115open")


class TestCloudDriveIngest:
    @pytest_asyncio.fixture
    async def repo(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        yield Repository(engine)
        await engine.dispose()

    @pytest.fixture
    def service(self, repo: Repository) -> WatcherService:
        return WatcherService(repo, EventBus(), use_polling=True, debounce_seconds=0, check_interval=0.05)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_clouddrive_skips_observer(
        self, service: WatcherService, repo: Repository, tmp_path: Path
    ) -> None:
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        assert service.is_running
        assert service._watcher is None
        assert lib.id in service._cloud_routes
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_create_registers(self, service: WatcherService, repo: Repository, tmp_path: Path) -> None:
        video = tmp_path / "MIDV-001.mp4"
        video.write_bytes(b"x" * 32)
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        await service.ingest_clouddrive(
            [
                CloudDriveChange(
                    action="create",
                    is_dir=False,
                    source_file="/115open/lib/MIDV-001.mp4",
                )
            ]
        )
        media = await repo.get_media_file_by_path(str(video))
        assert media is not None
        assert media.library_id == lib.id
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dir_create_scans_subtree(self, service: WatcherService, repo: Repository, tmp_path: Path) -> None:
        nested = tmp_path / "show"
        nested.mkdir()
        video = nested / "a.mp4"
        video.write_bytes(b"x" * 32)
        (nested / "readme.txt").write_text("no")
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        await service.ingest_clouddrive(
            [CloudDriveChange(action="create", is_dir=True, source_file="/115open/lib/show")]
        )
        assert await repo.get_media_file_by_path(str(video)) is not None
        assert await repo.get_media_file_by_path(str(nested / "readme.txt")) is None
        assert lib.id is not None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dir_delete_removes_prefix(self, service: WatcherService, repo: Repository, tmp_path: Path) -> None:
        nested = tmp_path / "show"
        nested.mkdir()
        video = nested / "a.mp4"
        video.write_bytes(b"x" * 32)
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        assert lib.id is not None
        await repo.create_media_file(library_id=lib.id, path=str(video))
        await service.start()
        await service.ingest_clouddrive(
            [CloudDriveChange(action="delete", is_dir=True, source_file="/115open/lib/show")]
        )
        assert await repo.get_media_file_by_path(str(video)) is None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_outside_prefix_ignored(self, service: WatcherService, repo: Repository, tmp_path: Path) -> None:
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        await service.ingest_clouddrive(
            [CloudDriveChange(action="create", is_dir=False, source_file="/115open/other/a.mp4")]
        )
        assert await repo.list_media_files(library_id=lib.id, limit=None) == []
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_native_observer_failure_keeps_cloud_routes(
        self, service: WatcherService, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        native_root = tmp_path / "native"
        cloud_root = tmp_path / "cloud"
        native_root.mkdir()
        cloud_root.mkdir()
        video = cloud_root / "a.mp4"
        video.write_bytes(b"x" * 32)
        await repo.create_library(
            name="native",
            path=str(native_root),
            automation=LibraryAutomation.WATCH,
        )
        cloud = await repo.create_library(
            name="cd",
            path=str(cloud_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )

        def boom(self: FileWatcher) -> None:
            raise OSError("inotify watch limit may be exceeded")

        monkeypatch.setattr(FileWatcher, "start", boom)
        await service.start()
        assert service.is_running
        assert service._watcher is None
        assert cloud.id in service._cloud_routes
        await service.ingest_clouddrive(
            [CloudDriveChange(action="create", is_dir=False, source_file="/115open/lib/a.mp4")]
        )
        assert await repo.get_media_file_by_path(str(video)) is not None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_rename_updates_path(self, service: WatcherService, repo: Repository, tmp_path: Path) -> None:
        src = tmp_path / "a.mp4"
        dest = tmp_path / "b.mp4"
        src.write_bytes(b"x" * 32)
        dest.write_bytes(b"x" * 32)
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        assert lib.id is not None
        await repo.create_media_file(library_id=lib.id, path=str(src))
        await service.start()
        await service.ingest_clouddrive(
            [
                CloudDriveChange(
                    action="rename",
                    is_dir=False,
                    source_file="/115open/lib/a.mp4",
                    destination_file="/115open/lib/b.mp4",
                )
            ]
        )
        assert await repo.get_media_file_by_path(str(src)) is None
        moved = await repo.get_media_file_by_path(str(dest))
        assert moved is not None
        assert moved.library_id == lib.id
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dir_rename_into_library_scans_once(self, repo: Repository, tmp_path: Path) -> None:
        service = WatcherService(repo, EventBus(), use_polling=True, debounce_seconds=0.3, check_interval=0.05)
        first = tmp_path / "a"
        second = tmp_path / "b"
        first.mkdir()
        second.mkdir()
        video_a = first / "a.mp4"
        video_b = second / "b.mp4"
        video_a.write_bytes(b"x" * 32)
        video_b.write_bytes(b"x" * 32)
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        started = time.monotonic()
        await service.ingest_clouddrive(
            [
                CloudDriveChange(
                    action="rename",
                    is_dir=True,
                    source_file="/115open/other/a",
                    destination_file="/115open/lib/a",
                ),
                CloudDriveChange(
                    action="rename",
                    is_dir=True,
                    source_file="/115open/other/b",
                    destination_file="/115open/lib/b",
                ),
            ]
        )
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert await repo.get_media_file_by_path(str(video_a)) is not None
        assert await repo.get_media_file_by_path(str(video_b)) is not None
        assert lib.id is not None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_rename_across_libraries(
        self, service: WatcherService, repo: Repository, tmp_path: Path
    ) -> None:
        src_root = tmp_path / "src"
        dest_root = tmp_path / "dest"
        src_root.mkdir()
        dest_root.mkdir()
        src = src_root / "a.mp4"
        dest = dest_root / "a.mp4"
        src.write_bytes(b"x" * 32)
        dest.write_bytes(b"x" * 32)
        src_lib = await repo.create_library(
            name="src",
            path=str(src_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/src",
            automation=LibraryAutomation.WATCH,
        )
        dest_lib = await repo.create_library(
            name="dest",
            path=str(dest_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/dest",
            automation=LibraryAutomation.WATCH,
        )
        assert src_lib.id is not None
        await repo.create_media_file(library_id=src_lib.id, path=str(src))
        await service.start()
        await service.ingest_clouddrive(
            [
                CloudDriveChange(
                    action="rename",
                    is_dir=False,
                    source_file="/115open/src/a.mp4",
                    destination_file="/115open/dest/a.mp4",
                )
            ]
        )
        assert await repo.get_media_file_by_path(str(src)) is None
        moved = await repo.get_media_file_by_path(str(dest))
        assert moved is not None
        assert moved.library_id == dest_lib.id
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dir_scan_skips_removed_library(self, repo: Repository, tmp_path: Path) -> None:
        service = WatcherService(repo, EventBus(), use_polling=True, debounce_seconds=0.2, check_interval=0.05)
        nested = tmp_path / "show"
        nested.mkdir()
        video = nested / "a.mp4"
        video.write_bytes(b"x" * 32)
        lib = await repo.create_library(
            name="cd",
            path=str(tmp_path),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        assert lib.id is not None
        await service.start()
        task = asyncio.create_task(
            service.ingest_clouddrive([CloudDriveChange(action="create", is_dir=True, source_file="/115open/lib/show")])
        )
        await asyncio.sleep(0.05)
        service.remove_library(lib.id)
        await repo.delete_library(lib.id)
        await task
        assert await repo.get_media_file_by_path(str(video)) is None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dir_rename_across_libraries_respects_dest_rules(
        self, service: WatcherService, repo: Repository, tmp_path: Path
    ) -> None:
        src_root = tmp_path / "src"
        dest_root = tmp_path / "dest"
        src_root.mkdir()
        dest_root.mkdir()
        nested = src_root / "show"
        nested.mkdir()
        video = nested / "a.mp4"
        video.write_bytes(b"x" * 32)
        dest_nested = dest_root / "show"
        dest_nested.mkdir()
        dest_video = dest_nested / "a.mp4"
        dest_video.write_bytes(b"x" * 32)
        src_lib = await repo.create_library(
            name="src",
            path=str(src_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/src",
            automation=LibraryAutomation.WATCH,
        )
        dest_lib = await repo.create_library(
            name="dest",
            path=str(dest_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/dest",
            automation=LibraryAutomation.WATCH,
            recursive=False,
        )
        assert src_lib.id is not None
        await repo.create_media_file(library_id=src_lib.id, path=str(video))
        await service.start()
        await service.ingest_clouddrive(
            [
                CloudDriveChange(
                    action="rename",
                    is_dir=True,
                    source_file="/115open/src/show",
                    destination_file="/115open/dest/show",
                )
            ]
        )
        assert await repo.get_media_file_by_path(str(video)) is None
        assert await repo.get_media_file_by_path(str(dest_video)) is None
        assert dest_lib.id is not None
        await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_add_library_observer_failure_clears_watcher(
        self, service: WatcherService, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cloud_root = tmp_path / "cloud"
        native_root = tmp_path / "native"
        cloud_root.mkdir()
        native_root.mkdir()
        await repo.create_library(
            name="cd",
            path=str(cloud_root),
            ingest=LibraryIngest.CLOUDDRIVE,
            cloud_path="/115open/lib",
            automation=LibraryAutomation.WATCH,
        )
        await service.start()
        assert service._watcher is None
        native = await repo.create_library(
            name="native",
            path=str(native_root),
            automation=LibraryAutomation.WATCH,
        )
        assert native.id is not None

        def boom(self: FileWatcher) -> None:
            raise OSError("inotify watch limit may be exceeded")

        monkeypatch.setattr(FileWatcher, "start", boom)
        service.add_library(str(native_root), native.id)
        assert service._watcher is None
        await service.stop()
