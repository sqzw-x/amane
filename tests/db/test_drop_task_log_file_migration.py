"""迁移: 删除 tasks.log_file 列. 任务日志路径由 id 派生, 不落库."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

from tests.helpers import alembic_config

_PREVIOUS = "136fd7914700"

_TASK_INDEXES = {"ix_tasks_type", "ix_tasks_status", "ix_tasks_root_task_id"}


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    return alembic_config(tmp_path / "migrate.db")


def _task_columns(conn: Connection) -> list[str]:
    return [row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))]


def test_drop_task_log_file(alembic_cfg: Config) -> None:
    """列删除走 batch 重建: 存量行、tasks 索引与 task_links 外键都必须保留."""
    command.upgrade(alembic_cfg, _PREVIOUS)

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)

    with engine.begin() as conn:
        assert "log_file" in _task_columns(conn)
        result = conn.execute(
            text(
                "INSERT INTO tasks (type, status, payload, log_file, retries, priority, created_at) "
                "VALUES ('scrape', 'done', '{\"number\": \"A-1\"}', 'tasks/task-1/task.log', 0, 0, "
                "CURRENT_TIMESTAMP)"
            )
        )
        task_id = int(result.lastrowid or 0)
        conn.execute(
            text(
                "INSERT INTO task_links (parent_task_id, child_task_id, key, created_at) "
                "VALUES (:task_id, :task_id, 'self', CURRENT_TIMESTAMP)"
            ),
            {"task_id": task_id},
        )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        assert "log_file" not in _task_columns(conn)
        assert conn.execute(text("SELECT id, type, status, priority FROM tasks")).all() == [
            (task_id, "scrape", "done", 0)
        ]
        assert {row[1] for row in conn.execute(text("PRAGMA index_list(tasks)"))} >= _TASK_INDEXES
        assert {row[2] for row in conn.execute(text("PRAGMA foreign_key_list(task_links)"))} == {"tasks"}
        assert conn.execute(text("SELECT parent_task_id FROM task_links")).scalars().all() == [task_id]

    command.downgrade(alembic_cfg, _PREVIOUS)

    with engine.connect() as conn:
        assert "log_file" in _task_columns(conn)
        assert conn.execute(text("SELECT log_file FROM tasks")).scalars().all() == [None]

    engine.dispose()
