"""SQLModel 表约束: 唯一性由 SQLite 强制, 不经 Repository."""

from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from amane.db.models import MediaFile, MediaFileStatus, Metadata, first_numeric_score


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        yield conn
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


class TestMediaFile:
    def test_path_is_unique(self, session: Session):
        media1 = MediaFile(path="/media/video/MIDV-123.mp4", library_id=1, status=MediaFileStatus.PENDING)
        media2 = MediaFile(path="/media/video/MIDV-123.mp4", library_id=1, status=MediaFileStatus.PENDING)
        session.add(media1)
        session.commit()
        session.add(media2)
        with pytest.raises(IntegrityError):
            session.commit()


class TestMetadata:
    def test_number_is_unique(self, session: Session):
        m1 = Metadata(number="MIDV-123")
        m2 = Metadata(number="MIDV-123")
        session.add(m1)
        session.commit()
        session.add(m2)
        with pytest.raises(IntegrityError):
            session.commit()

    @pytest.mark.parametrize(
        ("scores", "expected"),
        [
            ({}, None),
            ({"dmm": 7.5}, 7.5),
            ({"dmm": 0}, 0.0),
            ({"javdb": None, "dmm": 7.5}, 7.5),
            ({"javdb": "8.0"}, None),
            ({"javdb": {"nested": 1}, "dmm": 7.5}, 7.5),
        ],
    )
    def test_score_is_first_numeric_site(self, scores: dict[str, object], expected: float | None):
        """读取侧与物化列共用 ``first_numeric_score``: 首个数值, 非数值站点跳过而不是抛错."""
        meta = Metadata(number="MIDV-123")
        meta.scores = cast("dict[str, float]", scores)
        assert meta.score == expected
        assert first_numeric_score(scores) == expected
