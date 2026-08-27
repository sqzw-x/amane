"""进程级已迁移 SQLite 模板. 每测拷文件, 避免重复跑 24 个 Alembic revision.

xdist 每个 worker 各持一份; 首次 copy 时物化, 纯函数测试若不碰库则零开销.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from shutil import copy2

from amane.db.sqlite_migrate import upgrade_sqlite_database

_template: Path | None = None


def schema_template() -> Path:
    """已 vacuum 的 head schema, 进程内只建一次."""
    global _template
    if _template is None:
        directory = Path(tempfile.mkdtemp(prefix="amane-schema-"))
        path = directory / "template.db"
        upgrade_sqlite_database(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        _template = path
    return _template


def copy_schema(dest: Path) -> None:
    """把模板拷到 dest (覆盖已有文件). 连接级 PRAGMA 由调用方引擎自行设置."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    copy2(schema_template(), dest)
