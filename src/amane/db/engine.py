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

# 写锁等待上限. 驱动默认 5s, 并发 worker 与 API 同进程写单库、机械盘持锁时间长时不足.
BUSY_TIMEOUT_MS = 30_000


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
        # 这些设置都是连接级的: 每个新连接都必须重设, 漏设即退回驱动默认.
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # WAL 下掉电最多丢最近若干已提交事务且不损坏库, 换来提交不再逐笔 fsync.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
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
