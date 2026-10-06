"""迁移: 清掉已停用的回收任务历史行."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

from tests.helpers import alembic_config

_PREVIOUS = "fd5c8ad2c5db"


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    return alembic_config(tmp_path / "migrate.db")


def _insert_task(conn: Connection, task_type: str) -> int:
    result = conn.execute(
        text(
            "INSERT INTO tasks (type, status, payload, retries, priority, created_at) "
            "VALUES (:type, 'done', '{}', 0, 0, CURRENT_TIMESTAMP)"
        ),
        {"type": task_type},
    )
    return int(result.lastrowid or 0)


def test_drop_retired_trash_tasks(alembic_cfg: Config) -> None:
    """回收任务的历史行与其链上的边被删除; 其它类型的行与边不动."""
    command.upgrade(alembic_cfg, _PREVIOUS)

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)

    with engine.begin() as conn:
        trash_parent = _insert_task(conn, "trash")
        trash_child = _insert_task(conn, "trash")
        keep_parent = _insert_task(conn, "organize")
        keep_child = _insert_task(conn, "scrape")
        for parent, child in (
            (trash_parent, trash_child),
            (trash_parent, keep_child),
            (keep_parent, trash_child),
            (keep_parent, keep_child),
        ):
            conn.execute(
                text(
                    "INSERT INTO task_links (parent_task_id, child_task_id, key, created_at) "
                    "VALUES (:p, :c, :k, CURRENT_TIMESTAMP)"
                ),
                {"p": parent, "c": child, "k": f"{parent}-{child}"},
            )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        types = conn.execute(text("SELECT type FROM tasks ORDER BY id")).scalars().all()
        links = conn.execute(text("SELECT parent_task_id, child_task_id FROM task_links")).all()

    assert types == ["organize", "scrape"]
    assert links == [(keep_parent, keep_child)]

    engine.dispose()
