"""definition 档位合并: 相位列按当前 parser 复投影, 路径模板的映射键改名."""

import logging
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from amane.organize import validate_path_template
from tests.helpers import alembic_config


def test_definition_tiers_merged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, "e377abaad72f")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, recursive, patterns, video_template, poster_template) "
                "VALUES "
                "('t', '/m', 1, '[]', '{number}[[{def?|SD=标清,HD=高清}]].{ext}', '{number}.jpg'), "
                "('t2', '/m2', 1, '[]', '{number}[[{def?|720p=高清,HD=标清}]].{ext}', '{number}.jpg'), "
                "('t3', '/m3', 1, '[]', '{number}[[{def?|HD=标清,720p=高清}]].{ext}', '{number}.jpg'), "
                "('t4', '/m4', 1, '[]', '{number}.[{def?|SD=标清}]-[{def?|HD=高清}].{ext}', '{number}.jpg'), "
                "('t5', '/m5', 1, '[]', '{number}[[{def?|=缺省,SD=标清,480p=高清}]].{ext}', '{number}.jpg')"
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

        conn.execute(
            text(
                "INSERT INTO saved_queries (name, sql, entity, persisted, created_at, updated_at) "
                "VALUES ('legacy', \"SELECT number FROM metadata WHERE definition = 'HD'\", 'metadata', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    caplog.set_level(logging.INFO)
    command.upgrade(cfg, "e06def65f72a")

    with engine.connect() as conn:
        definitions = dict(conn.execute(text("SELECT path, definition FROM media_files")).all())
        templates = conn.execute(text("SELECT video_template FROM libraries ORDER BY id")).scalars().all()
        poster_template = conn.execute(text("SELECT poster_template FROM libraries ORDER BY id LIMIT 1")).scalar_one()
        saved_sql = conn.execute(text("SELECT sql FROM saved_queries")).scalar_one()

    assert definitions == {
        "/video/ABC-123-SD.mp4": "480p",
        "/video/ABC-124-HD.mp4": "720p",
        "/video/ABC-125-4320P.mp4": "8K",
    }
    assert templates == [
        "{number}[[{def?|480p=标清,720p=高清}]].{ext}",
        "{number}[[{def?|720p=高清}]].{ext}",
        "{number}[[{def?|720p=高清}]].{ext}",
        "{number}.[{def?|480p=标清}]-[{def?|720p=高清}].{ext}",
        "{number}[[{def?|=缺省,480p=高清}]].{ext}",
    ]
    for template in templates:
        validate_path_template(template)
    assert poster_template == "{number}.jpg"

    assert saved_sql == "SELECT number FROM metadata WHERE definition = 'HD'"

    messages = [record.getMessage() for record in caplog.records]
    assert not any("library=1 " in message for message in messages)
    for library_id, dropped in (("2", "HD->720p"), ("3", "HD->720p"), ("5", "SD->480p")):
        assert any(f"library={library_id} " in message and dropped in message for message in messages), (
            library_id,
            messages,
        )
    assert any("scanned=3 changed=3" in message for message in messages)
    assert any(
        "saved query compares legacy definition literal" in message and "ids=[1]" in message for message in messages
    )

    engine.dispose()
