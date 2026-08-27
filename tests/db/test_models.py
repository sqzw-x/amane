"""SQLModel 表定义测试"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from amane.db.models import (
    Library,
    MediaFile,
    MediaFileStatus,
    Metadata,
    RoutineType,
    Schedule,
    Task,
    TaskStatus,
    TaskType,
)
from amane.enums import DownloadableResource, LibraryAutomation, MoveMode
from amane.utils.extensions import DEFAULT_SUBTITLE_EXTENSIONS


@pytest.fixture
def engine():
    """创建内存 SQLite 引擎用于测试"""
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        yield conn
    engine.dispose()


@pytest.fixture
def session(engine):
    """创建测试用 session"""
    with Session(engine) as session:
        yield session


class TestMediaFile:
    def test_create_media_file(self, session: Session):
        media = MediaFile(
            path="/media/video/MIDV-123.mp4",
            library_id=1,
            oshash="abc123def456",
            size=1_500_000_000,
            duration=7200.0,
            codec="h264",
            number="MIDV-123",
            status=MediaFileStatus.PENDING,
        )
        session.add(media)
        session.commit()
        session.refresh(media)

        assert media.id is not None
        assert media.path == "/media/video/MIDV-123.mp4"
        assert media.status == MediaFileStatus.PENDING
        assert media.created_at is not None
        assert media.updated_at is not None

    def test_path_is_unique(self, session: Session):
        media1 = MediaFile(path="/media/video/MIDV-123.mp4", library_id=1, status=MediaFileStatus.PENDING)
        media2 = MediaFile(path="/media/video/MIDV-123.mp4", library_id=1, status=MediaFileStatus.PENDING)
        session.add(media1)
        session.commit()
        session.add(media2)
        with pytest.raises(IntegrityError):  # 唯一约束冲突
            session.commit()


class TestMetadata:
    def test_create_metadata(self, session: Session):
        meta = Metadata(
            number="MIDV-123",
            title="Test Title",
            actors=["Actor A", "Actor B"],
            studio="Studio X",
            publisher="Publisher Y",
            release="2025-01-15",
            runtime=120,
            tags=["tag1", "tag2"],
            series="Series Z",
            plot="A test plot.",
            scores={"javdb": 85.0},
            raw={"javdb": {"title": "Test Title"}},
            external_ids={"javdb": "abc", "dmm": "xyz"},
        )
        session.add(meta)
        session.commit()
        session.refresh(meta)

        assert meta.id is not None
        assert meta.number == "MIDV-123"
        assert meta.actors == ["Actor A", "Actor B"]
        assert meta.raw == {"javdb": {"title": "Test Title"}}

    def test_number_is_unique(self, session: Session):
        m1 = Metadata(number="MIDV-123")
        m2 = Metadata(number="MIDV-123")
        session.add(m1)
        session.commit()
        session.add(m2)
        with pytest.raises(IntegrityError):
            session.commit()


class TestTask:
    def test_create_task(self, session: Session):
        task = Task(
            type=TaskType.SCRAPE,
            status=TaskStatus.QUEUED,
            payload={"media_file_id": 1, "sites": ["javdb", "dmm"]},
            priority=5,
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        assert task.id is not None
        assert task.type == TaskType.SCRAPE
        assert task.status == TaskStatus.QUEUED
        assert task.retries == 0
        assert task.priority == 5

    def test_default_values(self, session: Session):
        task = Task(type=TaskType.REFRESH, status=TaskStatus.QUEUED, payload={})
        session.add(task)
        session.commit()
        session.refresh(task)

        assert task.retries == 0
        assert task.priority == 0


class TestLibrary:
    def test_create_library(self, session: Session):
        lib = Library(
            name="incoming",
            path="/media/incoming",
            automation=LibraryAutomation.SCRAPE,
            recursive=True,
            patterns=["*.mp4", "*.mkv", "*.avi"],
            video_template="/out/{studio}/{number}/{number}.{ext}",
        )
        session.add(lib)
        session.commit()
        session.refresh(lib)

        assert lib.id is not None
        assert lib.path == "/media/incoming"
        assert lib.patterns == ["*.mp4", "*.mkv", "*.avi"]
        assert lib.move_mode == MoveMode.MOVE
        assert lib.video_template == "/out/{studio}/{number}/{number}.{ext}"
        assert lib.write_nfo is True
        assert lib.copy_resources == [r for r in DownloadableResource if r != DownloadableResource.trailer]
        assert lib.trailer_pattern == "(?i)trailer"
        assert lib.automation == LibraryAutomation.SCRAPE
        assert lib.subtitle_extensions == list(DEFAULT_SUBTITLE_EXTENSIONS)


class TestSchedule:
    def test_create_schedule(self, session: Session):
        sched = Schedule(
            name="Nightly full scan",
            cron="0 3 * * *",
            task_type=RoutineType.CLEANUP,
            payload={"full": True},
            enabled=True,
        )
        session.add(sched)
        session.commit()
        session.refresh(sched)

        assert sched.id is not None
        assert sched.cron == "0 3 * * *"
        assert sched.task_type == RoutineType.CLEANUP


class TestMediaFileMetadataRelation:
    def test_link_media_to_metadata(self, session: Session):
        meta = Metadata(number="MIDV-123", title="Test")
        session.add(meta)
        session.commit()
        session.refresh(meta)

        media = MediaFile(
            path="/media/MIDV-123.mp4",
            library_id=1,
            number="MIDV-123",
            status=MediaFileStatus.SCRAPED,
            metadata_id=meta.id,
        )
        session.add(media)
        session.commit()
        session.refresh(media)

        assert media.metadata_id == meta.id

        # 反向查询
        stmt = select(MediaFile).where(MediaFile.metadata_id == meta.id)
        results = session.exec(stmt).all()
        assert len(results) == 1
        assert results[0].path == "/media/MIDV-123.mp4"
