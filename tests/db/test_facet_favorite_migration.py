"""分类收藏列: 四张分类实体表各加一列 ``is_favorite``, 既有行取默认假值."""

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import alembic_config

_PREVIOUS = "d9dec3f83da7"
_TABLES = ("tags", "studios", "publishers", "series")
_INSERT = (
    "INSERT INTO {table} (name, created_at, updated_at) VALUES (:name, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
)


def test_facet_favorite_columns(tmp_path: Path) -> None:
    """升级后既有行保留并取假值; batch 重建不得丢掉 ``name`` 的唯一索引与列默认值."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, _PREVIOUS)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(text(_INSERT.format(table=table)), {"name": f"{table}-keep"})

    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        inspector = inspect(conn)
        for table in _TABLES:
            assert "is_favorite" in {column["name"] for column in inspector.get_columns(table)}
            rows = conn.execute(text(f"SELECT name, is_favorite FROM {table}")).all()
            assert [(row[0], row[1]) for row in rows] == [(f"{table}-keep", 0)]

            # 不带该列写入的裸 SQL 走列默认值
            conn.execute(text(_INSERT.format(table=table)), {"name": f"{table}-raw"})
            assert (
                conn.execute(
                    text(f"SELECT is_favorite FROM {table} WHERE name = :name"), {"name": f"{table}-raw"}
                ).scalar_one()
                == 0
            )

            # SQLite 反射出的 unique 是 0/1, 不是布尔
            indexes = {index["name"]: index["unique"] for index in inspector.get_indexes(table)}
            assert indexes[f"ix_{table}_name"] == 1

    # 唯一索引在表重建后仍由数据库强制; 每张表独占一次事务, 失败不牵连后续断言
    for table in _TABLES:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(_INSERT.format(table=table)), {"name": f"{table}-keep"})
