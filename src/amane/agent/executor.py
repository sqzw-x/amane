from __future__ import annotations

from typing import Any

from ..db.models import SavedQuery, SavedQueryEntity
from .cache import CachedResult, ResultCache
from .sql import ReadonlySqlSandbox, SqlResult


def extract_entity_ids(columns: list[str], rows: list[list[Any]]) -> list[int]:
    """缺失 `id` 列则抛 ValueError."""
    lowered = [c.lower() for c in columns]
    if "id" not in lowered:
        raise ValueError("结果必须包含名为 id 的主键列")
    idx = lowered.index("id")
    ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        raw = row[idx]
        if raw is None:
            continue
        value = int(raw)
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


class QueryExecutor:
    def __init__(self, sandbox: ReadonlySqlSandbox, cache: ResultCache) -> None:
        self.sandbox = sandbox
        self.cache = cache

    async def run_sql(
        self,
        sql: str,
        *,
        timeout_ms: int,
        allow_slow: bool = False,
        approved: bool = False,
        max_rows: int | None = None,
    ) -> SqlResult:
        return await self.sandbox.execute(
            sql,
            timeout_ms=timeout_ms,
            allow_slow=allow_slow,
            approved=approved,
            max_rows=max_rows,
        )

    async def validate_saved_query(self, sql: str, *, entity: SavedQueryEntity, timeout_ms: int) -> None:
        """校验预设 SQL 可执行且满足实体契约; 截断结果不写缓存.

        ``entity`` 非 ``data`` 时结果须含 ``id`` 列 (缺失抛 ValueError).
        """
        result = await self.run_sql(sql, timeout_ms=timeout_ms, max_rows=1)
        if entity is not SavedQueryEntity.DATA:
            extract_entity_ids(result.columns, result.rows)

    async def ensure_cached(
        self,
        query: SavedQuery,
        *,
        timeout_ms: int,
        approved: bool = False,
    ) -> CachedResult:
        """未命中则执行 SQL 并写回缓存 (可能触发审批 / 重执行); 命中条件见 ``ResultCache.get``."""
        assert query.id is not None
        hit = self.cache.get(query.id, query.sql)
        if hit is not None:
            return hit
        result = await self.run_sql(query.sql, timeout_ms=timeout_ms, approved=approved)
        entry = CachedResult(saved_query_id=query.id, sql=query.sql, columns=result.columns, rows=result.rows)
        self.cache.put(entry)
        return entry
