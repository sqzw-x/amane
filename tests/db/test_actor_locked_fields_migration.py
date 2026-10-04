"""迁移: Actor.locked_fields 以 NOT NULL + '[]' 入库并回填存量行."""

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


def _insert_actor(conn: Any, *, name: str) -> int:
    result = conn.execute(
        text("INSERT INTO actors (name, created_at, updated_at) VALUES (:name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
        {"name": name},
    )
    return int(result.lastrowid)


def _index_sql(conn: Any, name: str) -> str:
    sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='index' AND name = :name"), {"name": name}
    ).scalar_one()
    assert isinstance(sql, str)
    return sql


def test_actor_locked_fields_backfill(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "e4f7dd7d4711")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        legacy_id = _insert_actor(conn, name="Legacy")

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"]: column for column in inspect(conn).get_columns("actors")}
        assert "locked_fields" in columns
        assert columns["locked_fields"]["nullable"] is False
        legacy = conn.execute(text("SELECT locked_fields FROM actors WHERE id = :id"), {"id": legacy_id}).scalar_one()
        assert legacy == "[]"
        # ADD COLUMN 不重建表, name 的唯一索引必须保留.
        index_sql = _index_sql(conn, "ix_actors_name")
        assert "UNIQUE" in index_sql

    # 新写入不给该列时由 server_default 填充.
    with engine.begin() as conn:
        new_id = _insert_actor(conn, name="New")
    with engine.connect() as conn:
        new = conn.execute(text("SELECT locked_fields FROM actors WHERE id = :id"), {"id": new_id}).scalar_one()
        assert new == "[]"

    engine.dispose()


def test_actor_locked_fields_downgrade_preserves_data_and_unique_index(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        actor_id = _insert_actor(conn, name="Downgrade")
        actor_id2 = _insert_actor(conn, name="AliasOwner")
        conn.execute(
            text(
                "INSERT INTO actor_aliases (actor_id, name, position, created_at, updated_at) "
                "VALUES (:actor_id, 'Alias', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"actor_id": actor_id2},
        )

    command.downgrade(alembic_cfg, "e4f7dd7d4711")

    with engine.connect() as conn:
        assert "locked_fields" not in {column["name"] for column in inspect(conn).get_columns("actors")}
        index_sql = _index_sql(conn, "ix_actors_name")
        assert "UNIQUE" in index_sql
        # batch 重建不得丢行或断开关联.
        assert (
            conn.execute(
                text("SELECT count(*) FROM actors WHERE id IN (:a, :b)"), {"a": actor_id, "b": actor_id2}
            ).scalar_one()
            == 2
        )
        assert (
            conn.execute(text("SELECT count(*) FROM actor_aliases WHERE actor_id = :a"), {"a": actor_id2}).scalar_one()
            == 1
        )

    engine.dispose()
