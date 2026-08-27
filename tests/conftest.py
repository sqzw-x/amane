"""全局测试 fixtures"""

from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from amane.db.repository import Repository
from amane.media import ResourceStore
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine


def _file_engine(db_path: Path) -> AsyncEngine:
    """文件 SQLite 引擎 (与生产同构: 多连接 + WAL).

    SQLite 自身通过连接级事务隔离 + WAL 控制并发写, 应用层无需加锁.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 5})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # 测试库数据可丢弃: 关闭 fsync 刷盘加速写入 (生产保持默认 FULL).
        cursor.execute("PRAGMA synchronous=OFF")
        # 旧内存库 (StaticPool) 不启用 FK; 保持该语义, 避免无归属 MediaFile 的用例集体失败.
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> AsyncGenerator[Repository]:
    """
    基于文件 SQLite 的异步 Repository (与生产同构).

    每个测试在 tmp_path 下独立数据库文件, 测试结束自动清理.
    """
    db_path = tmp_path / "amane.db"
    copy_schema(db_path)
    engine = _file_engine(db_path)
    yield Repository(engine)

    await engine.dispose()


@pytest_asyncio.fixture
async def resource_store(tmp_path: Path) -> AsyncGenerator[ResourceStore]:
    """独立的 ResourceStore (文件 DB + tmp 目录).

    handler 现强制注入 ResourceStore; 测试不触发真实下载时仅作占位.
    """
    db_path = tmp_path / "resources.db"
    copy_schema(db_path)
    engine = _file_engine(db_path)
    yield ResourceStore(engine=engine, base_dir=tmp_path / "resources")

    await engine.dispose()
