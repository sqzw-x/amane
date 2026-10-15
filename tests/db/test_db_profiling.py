"""数据库执行观测: 错误归类, 提交与语句计时, 日志限速."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from amane.db.engine import create_async_engine_from_path
from amane.db.profiling import (
    ERROR_LOG_INTERVAL_S,
    SLOW_COMMIT_MS,
    SLOW_STATEMENT_MS,
    SLOW_STATEMENTS_KEPT,
    DbErrorKind,
    DbMetrics,
    classify_db_error,
    install_profiling,
)


class _FakeClock:
    """错误日志限速只读时钟; 用例按秒推进, 不依赖真实等待."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_classify_db_error() -> None:
    """只有 SQLite 的锁类错误归 locked, 其余一律 other, 不得误判为可重试."""
    assert classify_db_error(sqlite3.OperationalError("database is locked")) is DbErrorKind.LOCKED
    assert classify_db_error(sqlite3.OperationalError("database table is locked")) is DbErrorKind.LOCKED
    assert classify_db_error(sqlite3.OperationalError("disk I/O error")) is DbErrorKind.OTHER
    assert classify_db_error(ValueError("boom")) is DbErrorKind.OTHER


def test_record_commit_threshold_and_max() -> None:
    """只有超过阈值的那次提交要求出日志; 计数全量, 最大值只涨不跌."""
    metrics = DbMetrics()

    assert metrics.record_commit(10.0) is False
    assert metrics.commits == 1
    assert metrics.max_commit_ms == 10.0
    assert metrics.slow_commits == 0

    assert metrics.record_commit(SLOW_COMMIT_MS + 1) is True
    assert metrics.slow_commits == 1
    assert metrics.max_commit_ms == SLOW_COMMIT_MS + 1

    assert metrics.record_commit(5.0) is False
    assert metrics.commits == 3
    assert metrics.max_commit_ms == SLOW_COMMIT_MS + 1


def test_record_statement_threshold_and_top_k() -> None:
    """阈值以内不留存; 超阈值的留存按耗时倒序, 只保前 K 条."""
    metrics = DbMetrics()

    assert metrics.record_statement(SLOW_STATEMENT_MS - 1, "SELECT fast") is False
    assert metrics.slow_statements == []

    for index in range(SLOW_STATEMENTS_KEPT + 2):
        assert metrics.record_statement(SLOW_STATEMENT_MS + index, f"SELECT slow {index}") in (True, False)

    kept = metrics.slow_statements
    assert len(kept) == SLOW_STATEMENTS_KEPT
    assert kept[0].statement == f"SELECT slow {SLOW_STATEMENTS_KEPT + 1}"
    assert kept[-1].statement == "SELECT slow 2"
    assert all(item.at is not None for item in kept)


def test_record_statement_log_rate_limit() -> None:
    """慢语句日志全局限速, 但留存不受限速影响."""
    clock = _FakeClock()
    metrics = DbMetrics(clock=clock)

    assert metrics.record_statement(SLOW_STATEMENT_MS + 1, "SELECT 1") is True
    assert metrics.record_statement(SLOW_STATEMENT_MS + 2, "SELECT 2") is False
    clock.advance(ERROR_LOG_INTERVAL_S)
    assert metrics.record_statement(SLOW_STATEMENT_MS + 3, "SELECT 3") is True
    assert [item.statement for item in metrics.slow_statements] == ["SELECT 3", "SELECT 2", "SELECT 1"]


def test_record_error_rate_limits_per_kind() -> None:
    """同类错误在窗口内只要求出一次日志; 计数与最后一次信息始终更新; 不同类别互不限速."""
    clock = _FakeClock()
    metrics = DbMetrics(clock=clock)

    assert metrics.record_error(DbErrorKind.LOCKED, "database is locked") is True
    assert metrics.record_error(DbErrorKind.LOCKED, "database is locked") is False
    assert metrics.record_error(DbErrorKind.OTHER, "boom") is True

    clock.advance(ERROR_LOG_INTERVAL_S - 1)
    assert metrics.record_error(DbErrorKind.LOCKED, "database is locked") is False
    clock.advance(1)
    assert metrics.record_error(DbErrorKind.LOCKED, "database is locked") is True

    locked = metrics.errors[DbErrorKind.LOCKED]
    assert locked.count == 4
    assert locked.last_message == "database is locked"
    assert locked.last_at is not None
    assert metrics.errors[DbErrorKind.OTHER].count == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_engine_factory_installs_metrics(tmp_path: Path) -> None:
    """工厂传入 metrics 时提交与语句被计数, 连接池占用可读."""
    metrics = DbMetrics()
    engine = await create_async_engine_from_path(tmp_path / "t.db", metrics=metrics)

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE t(x)")

    assert metrics.commits >= 1
    # 测试机上的建表远低于慢语句阈值, 因此不进入留存
    assert metrics.slow_statements == []
    assert metrics.pool_checked_out is not None
    await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_locked_write_is_classified_without_replacing_error(tmp_path: Path) -> None:
    """另一连接持写锁时: 归类为 locked, 计数 +1, 原异常类型不变地抛给调用方."""
    db = tmp_path / "locked.db"
    metrics = DbMetrics()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", connect_args={"timeout": 0.05})
    install_profiling(engine, metrics)

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

    stat = metrics.errors[DbErrorKind.LOCKED]
    assert stat.count == 1
    assert stat.last_message is not None and "locked" in stat.last_message.lower()
    assert metrics.pool_checked_out == 0

    # 锁释放后同一引擎仍可写入: 失败没有留下不可用的连接
    async with engine.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO t VALUES (3)")
    await engine.dispose()
