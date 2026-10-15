from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from .profiling import DbMetrics, install_profiling
from .sqlite_migrate import upgrade_sqlite_database

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()


async def create_async_engine_from_path(db_path: Path | str, *, metrics: DbMetrics | None = None) -> AsyncEngine:
    """先 ``upgrade_sqlite_database``, 再开业务引擎. 事务性 DDL 仅用于迁移连接.

    ``metrics`` 给出时挂上执行观测; 临时库与只读外部库不传.
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

    if metrics is not None:
        install_profiling(engine, metrics)

    logger.info("database ready", path=str(path))
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """``expire_on_commit=False``, 提交后仍可读取实例属性."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
