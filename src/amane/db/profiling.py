"""数据库慢路径日志: 阈值与限速.

只决定哪些事件写日志、一次日志涵盖多少条被抑制的事件; 不保存历史, 也不经接口下发.
只记 SQL 文本, 不记 ``parameters``. 见 docs/dev/observability.md.
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

# 超过该时长的提交与语句各记一条 WARNING.
SLOW_COMMIT_MS = 200.0
SLOW_STATEMENT_MS = 100.0
# 同一类事件两次日志之间的最小间隔; 窗口内被抑制的条数在下次日志里以 count 给出.
LOG_INTERVAL_S = 30.0
# 慢提交日志附带的本次事务语句轨迹.
_TRAIL_LIMIT = 5
# 日志里 SQL 文本与错误消息的留存长度: 完整语句与事务轨迹各一档.
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


class DbLogPolicy:
    """阈值与限速. 抑制窗口内的事件只累计条数, 由下一次日志的 ``count`` 交代.

    状态按事件类别定界 (错误类别最多 3 种), 不随事件数量增长. ``clock`` 供用例注入假时钟.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_error_log: dict[DbErrorKind, float] = {}
        self._suppressed_errors: dict[DbErrorKind, int] = {}
        self._last_slow_statement_log = float("-inf")
        self._suppressed_slow_statements = 0

    def slow_commit(self, duration_ms: float) -> bool:
        """提交超过 ``SLOW_COMMIT_MS`` 就写日志, 不限速."""
        return duration_ms >= SLOW_COMMIT_MS

    def slow_statement(self, duration_ms: float) -> int | None:
        """语句超过 ``SLOW_STATEMENT_MS`` 时返回窗口内累积条数; 未超阈值或限速中返回 None."""
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
        """返回该类别窗口内累积条数; 限速中返回 None. 不同类别互不影响."""
        self._suppressed_errors[kind] = self._suppressed_errors.get(kind, 0) + 1
        last = self._last_error_log.get(kind)
        if last is not None and self._clock() - last < LOG_INTERVAL_S:
            return None
        self._last_error_log[kind] = self._clock()
        return self._suppressed_errors.pop(kind)


def install_profiling(engine: AsyncEngine, policy: DbLogPolicy) -> None:
    """挂上监听. ``handle_error`` 返回 ``None``, 原异常照常抛出."""

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
