from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from typing import cast

import aiosqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel.ext.asyncio.session import AsyncSession

DEFAULT_ADVANCED_FILTER_TIMEOUT_SECONDS = 5.0
_PROGRESS_STEPS = 10_000


class QueryBudgetExceeded(RuntimeError):
    """SQLite interrupted a query after its request deadline."""


class QueryBudgetUnavailable(RuntimeError):
    """The configured database driver cannot enforce SQLite query budgets."""


async def _restore_progress_handler(connection: AsyncConnection, driver_connection: aiosqlite.Connection) -> None:
    # aiosqlite accepts None to clear the handler, but its type stub omits that overload.
    reset_task = asyncio.create_task(driver_connection.set_progress_handler(cast(Callable[[], int], None), 0))
    cancellation: asyncio.CancelledError | None = None
    failure: Exception | None = None
    while not reset_task.done():
        try:
            await asyncio.shield(reset_task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception as exc:
            failure = exc
            break
    if failure is None:
        try:
            reset_task.result()
        except Exception as exc:
            failure = exc
    if failure is not None:
        with suppress(Exception):
            await connection.invalidate()
        if cancellation is not None:
            raise cancellation
        raise QueryBudgetUnavailable("SQLite connection was discarded while clearing its progress handler") from failure
    if cancellation is not None:
        raise cancellation


@asynccontextmanager
async def sqlite_query_budget(
    session: AsyncSession,
    deadline: float | None,
    *,
    clock: Callable[[], float] | None = None,
) -> AsyncGenerator[None]:
    if deadline is None:
        yield
        return
    current_time = time.monotonic if clock is None else clock
    if deadline <= current_time():
        raise QueryBudgetExceeded("Advanced filter query exceeded its execution budget")

    connection = await session.connection()
    raw_connection = await connection.get_raw_connection()
    driver_connection = raw_connection.driver_connection
    if not isinstance(driver_connection, aiosqlite.Connection):
        raise QueryBudgetUnavailable("SQLite progress handler is unavailable for this database driver")

    def interrupt_after_deadline() -> int:
        return int(current_time() >= deadline)

    cancelled = False
    try:
        await driver_connection.set_progress_handler(interrupt_after_deadline, _PROGRESS_STEPS)
        try:
            yield
            if current_time() >= deadline:
                raise QueryBudgetExceeded("Advanced filter query exceeded its execution budget")
        except OperationalError as exc:
            original = exc.orig
            if isinstance(original, sqlite3.OperationalError) and original.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                raise QueryBudgetExceeded("Advanced filter query exceeded its execution budget") from exc
            raise
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        try:
            await _restore_progress_handler(connection, driver_connection)
        except QueryBudgetUnavailable:
            if not cancelled:
                raise
