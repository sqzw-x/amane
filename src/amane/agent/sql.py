"""只读 SQL 沙箱 - 单条查询, 超时, 只读连接, 解析层授权."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

# 安全边界由数据库层构成, 不需要词法校验 (正则既冗余又会误伤字面量):
# - ``mode=ro`` + ``PRAGMA query_only``: SQLite 拒绝主库与临时表的一切写语句;
# - ``sqlite3.execute`` 硬性单语句, 多语句直接报错;
# - authorizer (_readonly_authorizer): 解析层拒绝 ATTACH/DETACH 与 PRAGMA.
#   ``mode=ro`` 只约束 main 库, ATTACH 挂载的库默认以读写打开, VACUUM INTO
#   内部也经 ATTACH 授权动作 — 这两者是只读连接本身无法拦截的, 必须由
#   authorizer 拦截.


def _readonly_authorizer(action: int, arg1: str | None, *_rest: object) -> int:
    """解析层只读授权: 拒绝 ATTACH/DETACH 与除 query_only 外的 PRAGMA.

    ``query_only`` 是沙箱自身建立的连接态, 必须放行; 其余 PRAGMA (含
    journal_mode / schema_version 等写文件类) 一律拒绝.
    """
    del _rest  # 回调签名由 sqlite3 C 层固定, 多余位置参数无用
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and arg1 != "query_only":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SqlSandboxError(Exception):
    """SQL 执行失败 (含授权拒绝)."""


class SqlTimeoutError(SqlSandboxError):
    """查询超过超时."""


class SqlNeedsApproval(SqlSandboxError):
    """需要用户批准放宽超时后才能执行."""

    def __init__(self, sql: str, reason: str = "allow_slow") -> None:
        super().__init__(reason)
        self.sql = sql
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SqlResult:
    """只读查询结果; ``row_count`` 为 -1 表示截断 (实际至少已取这些行)."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: float


def as_id_subquery_sql(sql: str) -> str:
    """把 SQL 包成 ``SELECT id FROM (...) AS _sq``, 供列表 WHERE id IN (子查询).

    子查询语法只接受 SELECT/WITH, 非查询语句在语法层即失败 — 包装本身即约束.
    """
    normalized = sql.strip().rstrip(";").strip()
    return f"SELECT id FROM ({normalized}) AS _sq"


class ReadonlySqlSandbox:
    """对主库文件开只读 URI 连接执行查询."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def execute(
        self,
        sql: str,
        *,
        timeout_ms: int,
        allow_slow: bool = False,
        approved: bool = False,
        max_rows: int | None = None,
    ) -> SqlResult:
        """执行只读 SQL.

        ``allow_slow`` 且未 ``approved`` 时不执行, 抛 ``SqlNeedsApproval``.
        ``approved`` 时取消超时上限 (仍受调用方传入的 timeout_ms 约束; 批准路径传更大值).
        """
        if allow_slow and not approved:
            raise SqlNeedsApproval(sql)

        normalized = sql.strip().rstrip(";").strip()
        if not normalized:
            raise SqlSandboxError("SQL 为空")
        timeout_s = max(timeout_ms, 1) / 1000.0
        uri = self._db_path.resolve().as_uri() + "?mode=ro"

        started = time.perf_counter()
        try:
            return await asyncio.wait_for(self._run(uri, normalized, max_rows=max_rows), timeout=timeout_s)
        except TimeoutError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            raise SqlTimeoutError(f"查询超时 ({elapsed_ms:.0f}ms > {timeout_ms}ms)") from exc
        except sqlite3.Error as exc:
            # 列名/表名错误等必须变成工具可回收的 SqlSandboxError,
            # 否则会冒泡成 SSE StreamError 打断整轮 (模型无法自行修改 SQL 重试).
            raise SqlSandboxError(str(exc)) from exc

    async def _run(self, uri: str, sql: str, *, max_rows: int | None) -> SqlResult:
        started = time.perf_counter()
        async with aiosqlite.connect(uri, uri=True) as conn:
            await conn.execute("PRAGMA query_only = ON")
            await conn.set_authorizer(_readonly_authorizer)
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql)
            try:
                description = cursor.description or ()
                columns = [col[0] for col in description]
                if max_rows is None:
                    raw_rows = await cursor.fetchall()
                else:
                    raw_rows = await cursor.fetchmany(max_rows)
                rows = [list(row) for row in raw_rows]
                # row_count: 若截断则至少为已取行数; 全量时等于 len(rows)
                row_count = len(rows)
                if max_rows is not None and len(rows) == max_rows:
                    # 再取一行探测是否还有更多 - 不把探测行并入结果
                    more = list(await cursor.fetchmany(1))
                    if more:
                        # 未知精确总数时用 -1 表示"至少 max_rows"
                        row_count = -1
            finally:
                await cursor.close()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return SqlResult(columns=columns, rows=rows, row_count=row_count, elapsed_ms=elapsed_ms)
