"""definition 档位合并: 相位列按当前 parser 复投影, 路径模板的映射键改名."""

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from tests.helpers import alembic_config


def test_definition_tiers_merged(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, "e377abaad72f")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, recursive, patterns, video_template, poster_template) "
                "VALUES ('t', '/m', 1, '[]', '{number}[[{def?|SD=标清,HD=高清}]].{ext}', '{number}.jpg')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO media_files (path, status, library_id, created_at, updated_at) VALUES "
                "('/video/ABC-123-SD.mp4', 'scraped', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('/video/ABC-124-HD.mp4', 'scraped', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('/video/ABC-125-4320P.mp4', 'scraped', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(cfg, "e06def65f72a")

    with engine.connect() as conn:
        definitions = dict(conn.execute(text("SELECT path, definition FROM media_files")).all())
        video_template = conn.execute(text("SELECT video_template FROM libraries")).scalar_one()
        poster_template = conn.execute(text("SELECT poster_template FROM libraries")).scalar_one()

    assert definitions == {
        "/video/ABC-123-SD.mp4": "480p",
        "/video/ABC-124-HD.mp4": "720p",
        "/video/ABC-125-4320P.mp4": "8K",
    }
    assert video_template == "{number}[[{def?|480p=标清,720p=高清}]].{ext}"
    assert poster_template == "{number}.jpg"

    engine.dispose()
