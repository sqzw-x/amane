from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.toolsets import FunctionToolset

from ..db.models import SavedQueryEntity
from ..db.repository import Repository
from ..parsing import EMPTY_NUMBER_RULES, NumberRules
from .bridge import AgentRuntimeBridge
from .cache import CachedResult
from .executor import QueryExecutor, extract_entity_ids
from .sql import SqlNeedsApproval, SqlResult, SqlSandboxError, SqlTimeoutError

TOOL_OK = "OK"
"""成功回执: 模型下一步不需要任何观测的写操作只回它. 见 docs/dev/agent.md 返回值契约."""


def unknown_field_error(unknown: Iterable[str], allowed: Iterable[str]) -> dict[str, Any]:
    """patch 拒绝未知字段时一并回可写字段, 省掉一轮试探."""
    return {"error": f"不允许的字段: {', '.join(sorted(unknown))}; 可写字段: {', '.join(sorted(allowed))}"}


class ExploreResult(BaseModel):
    columns: list[str]
    sample_rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    saved_query_id: int | None = None
    """create_view 时物化的会话视图 id."""


class DeliverResult(BaseModel):
    saved_query_id: int
    name: str
    row_count: int


class InspectResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total: int


@dataclass
class AgentDeps:
    repo: Repository
    executor: QueryExecutor
    session_id: int
    sql_timeout_ms: int
    sample_limit: int = 20
    bridge: AgentRuntimeBridge = field(default_factory=AgentRuntimeBridge)
    number_rules: NumberRules = EMPTY_NUMBER_RULES


def require_approval(
    ctx: RunContext[AgentDeps],
    *,
    sql: str,
    tool: str,
    entity: SavedQueryEntity | None = None,
    name: str | None = None,
    create_view: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """未批准时 raise ApprovalRequired. metadata 供 UI 确认文案; approval 键为 tool_call_id."""
    if ctx.tool_call_approved:
        return
    tool_call_id = ctx.tool_call_id or ""
    if not tool_call_id:
        raise RuntimeError("require_approval 需要 tool_call_id")
    meta: dict[str, Any] = {
        "sql": sql,
        "tool": tool,
        "entity": entity if entity is not None else None,
        "name": name,
        "create_view": create_view,
        "extra": dict(extra or {}),
        "reason": "allow_slow",
    }
    raise ApprovalRequired(metadata=meta)


async def materialize_saved_query(
    deps: AgentDeps,
    *,
    sql: str,
    entity: SavedQueryEntity | None,
    name: str | None,
    description: str | None = None,
    result: SqlResult,
) -> tuple[int, str, list[int]]:
    """把全量 SQL 结果写成会话 SavedQuery 并入缓存.

    ``entity`` 省略按 ``DATA`` 落库: 不校验/不抽取 id, 只作数据表交付或探查视图.
    ``entity`` 为 metadata/actor 时按交付契约抽取 ``id`` 列 (缺失抛 ValueError).
    """
    entity = entity or SavedQueryEntity.DATA
    entity_ids: list[int] = []
    if entity is not SavedQueryEntity.DATA:
        entity_ids = extract_entity_ids(result.columns, result.rows)
    display_name = (name or "").strip() or ("数据查询" if entity is SavedQueryEntity.DATA else f"查询 ({entity})")
    saved = await deps.repo.create_saved_query(
        name=display_name,
        description=(description or "").strip(),
        sql=sql,
        entity=entity,
        session_id=deps.session_id,
        persisted=False,
    )
    assert saved.id is not None
    deps.executor.cache.put(CachedResult(saved_query_id=saved.id, sql=sql, columns=result.columns, rows=result.rows))
    return saved.id, saved.name, entity_ids


def build_explore_toolset() -> FunctionToolset[AgentDeps]:
    toolset: FunctionToolset[AgentDeps] = FunctionToolset()

    @toolset.tool
    async def sql_explore(
        ctx: RunContext[AgentDeps],
        sql: str,
        allow_slow: bool = False,
        create_view: bool = False,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Exploratory read-only SQL. Sample rows by default.

        Set create_view=True to materialize a session view for inspect_result pagination
        instead of hand-written LIMIT/OFFSET probes. Views are just row arrays — no entity
        or `id` column requirement. Views are not shown as UI chips.
        """
        try:
            result = await ctx.deps.executor.run_sql(
                sql,
                timeout_ms=ctx.deps.sql_timeout_ms,
                allow_slow=allow_slow,
                approved=ctx.tool_call_approved,
                max_rows=None if create_view else ctx.deps.sample_limit,
            )
        except SqlNeedsApproval:
            require_approval(
                ctx,
                sql=sql,
                tool="sql_explore",
                name=name,
                create_view=create_view,
            )
            raise  # pragma: no cover - require_approval 必 raise
        except (SqlSandboxError, SqlTimeoutError) as exc:
            return {"error": str(exc)}

        if create_view:
            try:
                view_id, _view_name, _entity_ids = await materialize_saved_query(
                    ctx.deps,
                    sql=sql,
                    entity=None,
                    name=name,
                    result=result,
                )
            except ValueError as exc:
                return {"error": str(exc)}
            out = ExploreResult(
                columns=result.columns,
                sample_rows=result.rows[: ctx.deps.sample_limit],
                row_count=len(result.rows),
                truncated=False,
                saved_query_id=view_id,
            )
        else:
            out = ExploreResult(
                columns=result.columns,
                sample_rows=result.rows[: ctx.deps.sample_limit],
                row_count=result.row_count if result.row_count >= 0 else len(result.rows),
                truncated=result.row_count < 0,
            )
        return out.model_dump(mode="json")

    @toolset.tool
    async def sql_deliver(
        ctx: RunContext[AgentDeps],
        sql: str,
        entity: SavedQueryEntity | None = None,
        name: str | None = None,
        description: str | None = None,
        allow_slow: bool = False,
    ) -> dict[str, Any]:
        """Deliver a final result set for humans. Creates a saved_query.

        name / description: short human-readable label and note, shown in the preset list
        and on the data page.
        entity=metadata|actor: SQL MUST return a column named `id`; the preset becomes a
        filter in /meta or /actors AND can be opened as a data table.
        Omit entity (or use data): any read-only result, rendered only as a data table.
        """
        try:
            result = await ctx.deps.executor.run_sql(
                sql,
                timeout_ms=ctx.deps.sql_timeout_ms,
                allow_slow=allow_slow,
                approved=ctx.tool_call_approved,
            )
        except SqlNeedsApproval:
            require_approval(ctx, sql=sql, tool="sql_deliver", entity=entity, name=name)
            raise  # pragma: no cover
        except (SqlSandboxError, SqlTimeoutError) as exc:
            return {"error": str(exc)}

        try:
            view_id, display_name, _ = await materialize_saved_query(
                ctx.deps,
                sql=sql,
                entity=entity,
                name=name,
                description=description,
                result=result,
            )
        except ValueError as exc:
            return {"error": str(exc)}

        out = DeliverResult(
            saved_query_id=view_id,
            name=display_name,
            row_count=len(result.rows),
        )
        return out.model_dump(mode="json")

    @toolset.tool
    async def inspect_result(
        ctx: RunContext[AgentDeps],
        saved_query_id: int,
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Inspect rows of a saved_query / explore view by id (uses cache or re-runs SQL)."""
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        query = await ctx.deps.repo.get_saved_query(saved_query_id)
        if query is None:
            return {"error": f"saved_query {saved_query_id} 不存在"}
        try:
            cached = await ctx.deps.executor.ensure_cached(query, timeout_ms=ctx.deps.sql_timeout_ms)
        except (SqlSandboxError, SqlTimeoutError) as exc:
            return {"error": str(exc)}

        out = InspectResult(
            columns=cached.columns,
            rows=cached.rows[offset : offset + limit],
            total=len(cached.rows),
        )
        return out.model_dump(mode="json")

    return toolset
