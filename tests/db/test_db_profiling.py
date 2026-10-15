"""数据库慢路径日志: 归类, 阈值与限速."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from amane.db.engine import create_async_engine_from_path
from amane.db.profiling import (
    LOG_INTERVAL_S,
    SLOW_COMMIT_MS,
    SLOW_STATEMENT_MS,
    DbErrorKind,
    DbLogPolicy,
    classify_db_error,
    install_profiling,
)


class _FakeClock:
    """只读时钟, 供限速判定; 用例按秒推进, 不等待真实时间."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_classify_db_error() -> None:
    """锁类错误归 locked, 其余归 other."""
    assert classify_db_error(sqlite3.OperationalError("database is locked")) is DbErrorKind.LOCKED
    assert classify_db_error(sqlite3.OperationalError("database table is locked")) is DbErrorKind.LOCKED
    assert classify_db_error(sqlite3.OperationalError("disk I/O error")) is DbErrorKind.OTHER
    assert classify_db_error(ValueError("boom")) is DbErrorKind.OTHER


def test_slow_commit_threshold() -> None:
    """提交仅在达到阈值时要求记录日志, 不参与限速."""
    policy = DbLogPolicy()

    assert policy.slow_commit(SLOW_COMMIT_MS - 1) is False
    assert policy.slow_commit(SLOW_COMMIT_MS) is True
    assert policy.slow_commit(SLOW_COMMIT_MS * 10) is True


def test_slow_statement_threshold_and_suppressed_count() -> None:
    """阈值内的语句不要求记录日志; 被抑制的条数由窗口结束后的那次日志交代."""
    clock = _FakeClock()
    policy = DbLogPolicy(clock=clock)

    assert policy.slow_statement(SLOW_STATEMENT_MS - 1) is None
    # 首次立即记录, count 为 1
    assert policy.slow_statement(SLOW_STATEMENT_MS) == 1
    # 窗口内被抑制, 但仍计入累计
    assert policy.slow_statement(SLOW_STATEMENT_MS) is None
    assert policy.slow_statement(SLOW_STATEMENT_MS) is None
    clock.advance(LOG_INTERVAL_S)
    # 含本次在内, 共 3 条
    assert policy.slow_statement(SLOW_STATEMENT_MS) == 3


def test_error_rate_limit_is_per_kind() -> None:
    """错误按类别分别限速, 互不影响; 累计条数由下一次日志给出."""
    clock = _FakeClock()
    policy = DbLogPolicy(clock=clock)

    assert policy.error(DbErrorKind.LOCKED) == 1
    assert policy.error(DbErrorKind.LOCKED) is None
    assert policy.error(DbErrorKind.OTHER) == 1
    assert policy.error(DbErrorKind.LOCKED) is None

    clock.advance(LOG_INTERVAL_S - 1)
    assert policy.error(DbErrorKind.LOCKED) is None
    clock.advance(1)
    # 上次日志之后被抑制的 3 条, 加上本条共 4 条
    assert policy.error(DbErrorKind.LOCKED) == 4


@pytest.mark.asyncio(loop_scope="function")
async def test_engine_factory_installs_policy(tmp_path: Path) -> None:
    """传入 policy 时注册了监听, 且不影响既有 pragma."""
    policy = DbLogPolicy()
    engine = await create_async_engine_from_path(tmp_path / "t.db", policy=policy)

    async with engine.begin() as conn:
        assert (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar() == "wal"
        assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        await conn.exec_driver_sql("CREATE TABLE t(x)")

    await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_locked_write_is_classified_without_replacing_error(tmp_path: Path) -> None:
    """另一连接持写锁时归为 locked, 原异常类型不变; 释放后同一引擎仍可写入."""
    db = tmp_path / "locked.db"
    policy = DbLogPolicy()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", connect_args={"timeout": 0.05})
    install_profiling(engine, policy)

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE t(x)")

    holder = sqlite3.connect(db)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")

    with pytest.raises(OperationalError) as excinfo:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("INSERT INTO t VALUES (2)")
    assert "locked" in str(excinfo.value).lower()

    holder.rollback()
    holder.close()

    async with engine.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO t VALUES (3)")
    await engine.dispose()
