"""CloudDrive webhook 分流与入库."""

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
