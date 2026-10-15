"""数据库执行观测: 错误归类 + 提交与语句计时.

计数始终精确; 日志按阈值与限速输出. 只记 SQL 文本, 不记 ``parameters``.
挂在业务引擎上, 见 docs/dev/observability.md.
"""

import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event
from sqlalchemy.pool import Pool, QueuePool

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, ExceptionContext, ExecutionContext
    from sqlalchemy.engine.interfaces import DBAPICursor
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger()

# 超过该时长的提交与语句各记一条 WARNING; 阈值以内只累加计数.
SLOW_COMMIT_MS = 200.0
SLOW_STATEMENT_MS = 100.0
# 同类日志之间的最小间隔 (错误按类别, 慢语句全局), 防止一次性故障冲垮日志.
ERROR_LOG_INTERVAL_S = 30.0
# 留存供排查的慢语句条数, 与拼接日志时保留的事务语句轨迹.
SLOW_STATEMENTS_KEPT = 5
_TRAIL_LIMIT = 5
# SQL 文本的留存长度: 完整语句与日志里的轨迹各一档.
_SNIPPET_LIMIT = 200
_TRAIL_SNIPPET = 100
_LOCKED_MESSAGES = ("database is locked", "database table is locked")
_TXN_T0_KEY = "_amane_db_txn_t0"
_TXN_TRAIL_KEY = "_amane_db_txn_trail"
_STMT_T0_KEY = "_amane_db_stmt_t0"


class DbErrorKind(StrEnum):
    """数据库错误类别. ``locked`` 供后续的锁错误有界重试复用."""

    LOCKED = "locked"
    DISCONNECT = "disconnect"
    OTHER = "other"


@dataclass
class DbErrorStat:
    count: int = 0
    last_message: str | None = None
    last_at: datetime | None = None


@dataclass
class SlowStatement:
    """留存的一条慢语句, 按耗时倒序最多保留 ``SLOW_STATEMENTS_KEPT`` 条."""

    statement: str
    duration_ms: float
    at: datetime


def classify_db_error(exc: BaseException) -> DbErrorKind:
    """只认 SQLite 的锁类错误; 其余归 ``other``, 由调用方决定是否重试."""
    if isinstance(exc, sqlite3.OperationalError) and any(m in str(exc).lower() for m in _LOCKED_MESSAGES):
        return DbErrorKind.LOCKED
    return DbErrorKind.OTHER


def _snippet(text: str) -> str:
    return text if len(text) <= _SNIPPET_LIMIT else f"{text[:_SNIPPET_LIMIT]}…"


def _append_trail(conn: Connection, statement: str) -> None:
    """记录本次事务内执行过的语句, 供慢提交日志指出是哪几条."""
    trail = conn.info.setdefault(_TXN_TRAIL_KEY, [])
    if len(trail) < _TRAIL_LIMIT:
        trail.append(statement[:_TRAIL_SNIPPET])


class DbMetrics:
    """进程内计数与极值; 重启清零, 不持久化.

    ``clock`` 只用于日志限速, 供用例注入假时钟.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pool: QueuePool | None = None
        self._last_error_log: dict[DbErrorKind, float] = {}
        self._last_slow_statement_log = float("-inf")
        self.commits = 0
        self.slow_commits = 0
        self.max_commit_ms: float | None = None
        self.slow_statements: list[SlowStatement] = []
        self.errors: dict[DbErrorKind, DbErrorStat] = {}

    def bind_pool(self, pool: Pool) -> None:
        """连接池占用只在队列池上可读; 其它池型留空."""
        if isinstance(pool, QueuePool):
            self._pool = pool

    @property
    def pool_checked_out(self) -> int | None:
        return None if self._pool is None else self._pool.checkedout()

    def record_commit(self, duration_ms: float) -> bool:
        """返回是否应当输出日志 (超过 ``SLOW_COMMIT_MS``)."""
        self.commits += 1
        if self.max_commit_ms is None or duration_ms > self.max_commit_ms:
            self.max_commit_ms = duration_ms
        if duration_ms < SLOW_COMMIT_MS:
            return False
        self.slow_commits += 1
        return True

    def record_statement(self, duration_ms: float, statement: str) -> bool:
        """超过 ``SLOW_STATEMENT_MS`` 时留存该语句; 返回是否应当输出日志 (全局限速)."""
        if duration_ms < SLOW_STATEMENT_MS:
            return False
        self.slow_statements.append(
            SlowStatement(statement=_snippet(statement), duration_ms=duration_ms, at=datetime.now(UTC))
        )
        self.slow_statements.sort(key=lambda item: item.duration_ms, reverse=True)
        del self.slow_statements[SLOW_STATEMENTS_KEPT:]
        now = self._clock()
        if now - self._last_slow_statement_log < ERROR_LOG_INTERVAL_S:
            return False
        self._last_slow_statement_log = now
        return True

    def record_error(self, kind: DbErrorKind, message: str) -> bool:
        """返回是否应当输出日志 (同类错误受 ``ERROR_LOG_INTERVAL_S`` 限速)."""
        stat = self.errors.setdefault(kind, DbErrorStat())
        stat.count += 1
        stat.last_message = _snippet(message)
        stat.last_at = datetime.now(UTC)
        now = self._clock()
        last = self._last_error_log.get(kind)
        if last is not None and now - last < ERROR_LOG_INTERVAL_S:
            return False
        self._last_error_log[kind] = now
        return True


def install_profiling(engine: AsyncEngine, metrics: DbMetrics) -> None:
    """挂上观测监听. ``handle_error`` 返回 ``None``, 原异常照常抛出."""
    metrics.bind_pool(engine.pool)

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Connection) -> None:
        conn.info[_TXN_T0_KEY] = time.monotonic()
        conn.info[_TXN_TRAIL_KEY] = []

    @event.listens_for(engine.sync_engine, "commit")
    def _commit(conn: Connection) -> None:
        t0 = conn.info.pop(_TXN_T0_KEY, None)
        trail: list[str] = conn.info.pop(_TXN_TRAIL_KEY, [])
        if t0 is None:
            return
        duration_ms = (time.monotonic() - t0) * 1000
        if metrics.record_commit(duration_ms):
            # 事务里执行过的语句, 用于判断是什么拖住了这次提交
            logger.warning("slow db commit", duration_ms=round(duration_ms, 1), statements=" | ".join(trail))

    @event.listens_for(engine.sync_engine, "rollback")
    def _rollback(conn: Connection) -> None:
        conn.info.pop(_TXN_T0_KEY, None)
        conn.info.pop(_TXN_TRAIL_KEY, None)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(
        conn: Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        conn.info[_STMT_T0_KEY] = time.monotonic()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(
        conn: Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        t0 = conn.info.pop(_STMT_T0_KEY, None)
        _append_trail(conn, statement)
        if t0 is None:
            return
        duration_ms = (time.monotonic() - t0) * 1000
        if metrics.record_statement(duration_ms, statement):
            logger.warning("slow db statement", duration_ms=round(duration_ms, 1), statement=_snippet(statement))

    @event.listens_for(engine.sync_engine, "handle_error")
    def _error(context: ExceptionContext) -> None:
        kind = DbErrorKind.DISCONNECT if context.is_disconnect else classify_db_error(context.original_exception)
        message = str(context.original_exception)
        if not metrics.record_error(kind, message):
            return
        log = logger.warning if kind is DbErrorKind.LOCKED else logger.error
        log(
            "database error",
            kind=str(kind),
            message=message,
            statement=_snippet(context.statement or ""),
            count=metrics.errors[kind].count,
            exc_info=context.sqlalchemy_exception,
        )
