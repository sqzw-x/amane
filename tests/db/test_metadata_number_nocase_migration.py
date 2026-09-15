"""迁移: Metadata.number 唯一索引改为 COLLATE NOCASE, 合并仅大小写不同的重复行."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import alembic_config


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    return alembic_config(tmp_path / "migrate.db")


def _insert_metadata(conn: Any, *, number: str, title: str) -> int:
    result = conn.execute(
        text(
            "INSERT INTO metadata (number, title, actors, directors, tags, studio, publisher, series, "
            "poster_urls, thumb_urls, trailer_urls, extrafanart_urls, scores, external_ids, source_urls, "
            "field_sources, raw, created_at, updated_at) "
            "VALUES (:number, :title, '[]', '[]', '[]', NULL, NULL, NULL, "
            "'[]', '[]', '[]', '{}', '{}', '{}', '{}', '{}', '{}', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"number": number, "title": title},
    )
    return int(result.lastrowid)


def test_metadata_number_case_insensitive_unique_merges_duplicates(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "c4f17334c3ea")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)

    with engine.begin() as conn:
        keeper_id = _insert_metadata(conn, number="ABC-001", title="Keep")
        dup_id = _insert_metadata(conn, number="abc-001", title="Dup")
        assert keeper_id != dup_id

        conn.execute(
            text(
                "INSERT INTO actors (name, created_at, updated_at) "
                "VALUES ('Alice', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        actor_id = int(conn.execute(text("SELECT id FROM actors WHERE name = 'Alice'")).scalar_one())
        conn.execute(
            text("INSERT INTO metadata_actors (metadata_id, actor_id, position) VALUES (:m, :a, 0)"),
            {"m": keeper_id, "a": actor_id},
        )
        conn.execute(
            text("INSERT INTO metadata_actors (metadata_id, actor_id, position) VALUES (:m, :a, 0)"),
            {"m": dup_id, "a": actor_id},
        )
        conn.execute(
            text(
                "INSERT INTO comments (metadata_id, body, created_at, updated_at) "
                "VALUES (:m, 'from-dup', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"m": dup_id},
        )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        numbers = [row[0] for row in conn.execute(text("SELECT number FROM metadata ORDER BY id")).fetchall()]
        assert numbers == ["ABC-001"]

        comment_meta = conn.execute(text("SELECT metadata_id FROM comments WHERE body = 'from-dup'")).scalar_one()
        assert int(comment_meta) == keeper_id

        link_count = conn.execute(
            text("SELECT COUNT(*) FROM metadata_actors WHERE metadata_id = :m"), {"m": keeper_id}
        ).scalar_one()
        assert int(link_count) == 1

    with pytest.raises(IntegrityError), engine.begin() as write:
        _insert_metadata(write, number="Abc-001", title="ShouldFail")
