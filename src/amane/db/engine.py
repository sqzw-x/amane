from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from .profiling import DbLogPolicy, install_profiling
from .sqlite_migrate import upgrade_sqlite_database

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()


async def create_async_engine_from_path(db_path: Path | str, *, policy: DbLogPolicy | None = None) -> AsyncEngine:
    """先 ``upgrade_sqlite_database``, 再开业务引擎. 事务性 DDL 仅用于迁移连接.

    ``policy`` 给出时注册慢路径日志.
    """
    path = Path(db_path)
    try:
        upgrade_sqlite_database(path)
    except Exception as e:
        raise RuntimeError(f"Database migration failed: {e}") from e

    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        # 每个新连接启用 WAL 并开启外键约束
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if policy is not None:
        install_profiling(engine, policy)

    logger.info("database ready", path=str(path))
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """``expire_on_commit=False``, 提交后仍可读取实例属性."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
