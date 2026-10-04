"""迁移: Metadata.locked_fields 以 NOT NULL + '[]' 入库并回填存量行."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


def test_metadata_locked_fields_backfill(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "0c4de00792e3")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        legacy_id = _insert_metadata(conn, number="ABC-001", title="Legacy")

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"]: column for column in inspect(conn).get_columns("metadata")}
        assert "locked_fields" in columns
        assert columns["locked_fields"]["nullable"] is False
        legacy = conn.execute(text("SELECT locked_fields FROM metadata WHERE id = :id"), {"id": legacy_id}).scalar_one()
        assert legacy == "[]"

    # 新写入不给该列时由 server_default 填充.
    with engine.begin() as conn:
        new_id = _insert_metadata(conn, number="ABC-002", title="New")
    with engine.connect() as conn:
        new = conn.execute(text("SELECT locked_fields FROM metadata WHERE id = :id"), {"id": new_id}).scalar_one()
        assert new == "[]"

    engine.dispose()
