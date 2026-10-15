"""数据库慢路径日志: 事件判定、限速与监听安装.

只记录 SQL 文本, 不记录 ``parameters`` (可能含用户数据); 日志去向与覆盖范围见 docs/dev/observability.md.
"""

import sqlite3
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, ExceptionContext, ExecutionContext
    from sqlalchemy.engine.interfaces import DBAPICursor
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger()

# 超过该时长各记一条 warning: 提交不限速, 语句受 LOG_INTERVAL_S 约束.
SLOW_COMMIT_MS = 200.0
SLOW_STATEMENT_MS = 100.0
# 同一类别两次日志之间的最小间隔; 窗口内被抑制的条数由下一次日志的 count 交代.
LOG_INTERVAL_S = 30.0
# 慢提交日志附带的事务语句轨迹: 条数上限与单条留存长度.
_TRAIL_LIMIT = 5
_TRAIL_SNIPPET = 100
# 慢语句与错误消息的留存长度.
_SNIPPET_LIMIT = 200
_LOCKED_MESSAGES = ("database is locked", "database table is locked")
_TXN_T0_KEY = "_amane_db_txn_t0"
_TXN_TRAIL_KEY = "_amane_db_txn_trail"
_STMT_T0_KEY = "_amane_db_stmt_t0"


class DbErrorKind(StrEnum):
    """数据库错误类别; 限速按类别分别统计."""

    LOCKED = "locked"
    DISCONNECT = "disconnect"
    OTHER = "other"


def classify_db_error(exc: BaseException) -> DbErrorKind:
    """SQLite 锁类错误归 ``locked``, 其余归 ``other``."""
    if isinstance(exc, sqlite3.OperationalError) and any(m in str(exc).lower() for m in _LOCKED_MESSAGES):
        return DbErrorKind.LOCKED
    return DbErrorKind.OTHER


def _snippet(text: str) -> str:
    return text if len(text) <= _SNIPPET_LIMIT else f"{text[:_SNIPPET_LIMIT]}…"


def _append_trail(conn: Connection, statement: str) -> None:
    """把本次事务执行过的语句追加进 ``conn.info``, 供慢提交日志引用."""
    trail = conn.info.setdefault(_TXN_TRAIL_KEY, [])
    if len(trail) < _TRAIL_LIMIT:
        trail.append(statement[:_TRAIL_SNIPPET])


class DbLogPolicy:
    """阈值判定与限速. 状态按键定界, 不随事件数量增长.

    ``clock`` 供用例注入假时钟.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_error_log: dict[DbErrorKind, float] = {}
        self._suppressed_errors: dict[DbErrorKind, int] = {}
        self._last_slow_statement_log = float("-inf")
        self._suppressed_slow_statements = 0

    def slow_commit(self, duration_ms: float) -> bool:
        """提交时长是否达到 ``SLOW_COMMIT_MS``; 提交日志不参与限速."""
        return duration_ms >= SLOW_COMMIT_MS

    def slow_statement(self, duration_ms: float) -> int | None:
        """语句达到 ``SLOW_STATEMENT_MS`` 时返回窗口内累积条数; 未达阈值或处于限速窗口内返回 ``None``."""
        if duration_ms < SLOW_STATEMENT_MS:
            return None
        self._suppressed_slow_statements += 1
        if self._clock() - self._last_slow_statement_log < LOG_INTERVAL_S:
            return None
        self._last_slow_statement_log = self._clock()
        count = self._suppressed_slow_statements
        self._suppressed_slow_statements = 0
        return count

    def error(self, kind: DbErrorKind) -> int | None:
        """返回该类别窗口内累积条数; 处于限速窗口内返回 ``None``."""
        self._suppressed_errors[kind] = self._suppressed_errors.get(kind, 0) + 1
        last = self._last_error_log.get(kind)
        if last is not None and self._clock() - last < LOG_INTERVAL_S:
            return None
        self._last_error_log[kind] = self._clock()
        return self._suppressed_errors.pop(kind)


def install_profiling(engine: AsyncEngine, policy: DbLogPolicy) -> None:
    """注册监听. ``handle_error`` 返回 ``None``, 原异常照常抛给调用方."""

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
        if policy.slow_commit(duration_ms):
            # 事务内执行过的语句轨迹, 用于定位提交耗时的来源
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
        count = policy.slow_statement(duration_ms)
        if count is not None:
            logger.warning(
                "slow db statement",
                duration_ms=round(duration_ms, 1),
                statement=_snippet(statement),
                count=count,
            )

    @event.listens_for(engine.sync_engine, "handle_error")
    def _error(context: ExceptionContext) -> None:
        kind = DbErrorKind.DISCONNECT if context.is_disconnect else classify_db_error(context.original_exception)
        count = policy.error(kind)
        if count is None:
            return
        log = logger.warning if kind is DbErrorKind.LOCKED else logger.error
        log(
            "database error",
            kind=str(kind),
            message=_snippet(str(context.original_exception)),
            statement=_snippet(context.statement or ""),
            count=count,
            exc_info=context.sqlalchemy_exception,
        )
