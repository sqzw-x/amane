"""pydantic-ai 工具: sql_explore / sql_deliver / inspect_result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.toolsets import FunctionToolset

from amane.db.models import SavedQueryEntity
from amane.db.repository import Repository

from .bridge import AgentRuntimeBridge
from .cache import CachedResult
from .executor import QueryExecutor, extract_entity_ids
from .sql import SqlNeedsApproval, SqlResult, SqlSandboxError, SqlTimeoutError
from .trace import SessionTrace, TraceEvent


class NeedsApprovalPayload(BaseModel):
    """SSE / UI 待批载荷; approval_id 实际为 tool_call_id."""

    approval_id: str
    sql: str
    tool: str
    entity: SavedQueryEntity | None = None
    name: str | None = None
    create_view: bool = False
    reason: str = "allow_slow"


class ExploreResult(BaseModel):
    columns: list[str]
    sample_rows: list[list[Any]]
    row_count: int
    elapsed_ms: float
    truncated: bool = False
    saved_query_id: int | None = None
    """create_view 时物化的会话视图 id; 可用 inspect_result 翻页."""
    id_count: int | None = None


class DeliverResult(BaseModel):
    saved_query_id: int
    name: str
    entity: SavedQueryEntity
    id_count: int
    columns: list[str]
    sample_rows: list[list[Any]]
    elapsed_ms: float


class InspectResult(BaseModel):
    saved_query_id: int
    columns: list[str]
    rows: list[list[Any]]
    offset: int
    limit: int
    total_ids: int


@dataclass
class PendingApproval:
    """进程内待批快照; approval_id == tool_call_id."""

    approval_id: str
    session_id: int
    sql: str
    tool: str
    entity: SavedQueryEntity | None = None
    name: str | None = None
    create_view: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDeps:
    repo: Repository
    executor: QueryExecutor
    session_id: int
    trace: SessionTrace
    sql_timeout_ms: int
    sample_limit: int = 20
    pending: dict[str, PendingApproval] = field(default_factory=dict)
    last_saved_query_ids: list[int] = field(default_factory=list)
    awaiting_approval: NeedsApprovalPayload | None = None
    persist_tool_trace: bool = True
    """流式回合由 SSE 落盘工具事件时为 False, 避免重复."""
    bridge: AgentRuntimeBridge = field(default_factory=AgentRuntimeBridge)
    """库路径 / watcher / 取消运行任务等写面桥接."""


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
    """未批准时 raise ApprovalRequired; 已批准则直接返回让工具体继续执行.

    metadata 供 UI 确认文案; approval 键为 tool_call_id.
    """
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
    ctx.deps.pending[tool_call_id] = PendingApproval(
        approval_id=tool_call_id,
        session_id=ctx.deps.session_id,
        sql=sql,
        tool=tool,
        entity=entity,
        name=name,
        create_view=create_view,
        extra=dict(extra or {}),
    )
    raise ApprovalRequired(metadata=meta)


def trace_tool(ctx: RunContext[AgentDeps], event_type: str, payload: dict[str, Any]) -> None:
    if ctx.deps.persist_tool_trace:
        ctx.deps.trace.append(TraceEvent(type=event_type, payload=payload))


async def materialize_saved_query(
    deps: AgentDeps,
    *,
    sql: str,
    entity: SavedQueryEntity | None,
    name: str | None,
    result: SqlResult,
    surface_to_user: bool,
) -> tuple[int, str, list[int]]:
    """把全量 SQL 结果写成会话 SavedQuery 并入缓存.

    ``entity`` 省略按 ``DATA`` 落库: 不校验/不抽取 id, 只作数据表交付或探查视图.
    ``entity`` 为 metadata/actor 时按交付契约抽取 ``id`` 列 (缺失抛 ValueError).
    ``surface_to_user=True`` 时追加 ``last_saved_query_ids`` (交付芯片); 探查视图为 False.
    """
    entity = entity or SavedQueryEntity.DATA
    entity_ids: list[int] = []
    if entity is not SavedQueryEntity.DATA:
        entity_ids = extract_entity_ids(result.columns, result.rows)
    display_name = (name or "").strip() or ("数据查询" if entity is SavedQueryEntity.DATA else f"查询 ({entity})")
    saved = await deps.repo.create_saved_query(
        name=display_name,
        sql=sql,
        entity=entity,
        session_id=deps.session_id,
        persisted=False,
    )
    assert saved.id is not None
    deps.executor.cache.put(CachedResult(saved_query_id=saved.id, columns=result.columns, rows=result.rows))
    if surface_to_user:
        deps.last_saved_query_ids.append(saved.id)
    return saved.id, saved.name, entity_ids


def build_explore_toolset() -> FunctionToolset[AgentDeps]:
    """只读探查/交付/检视工具集 (始终暴露)."""
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
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "sql_explore", "sql": sql, "allow_slow": allow_slow, "create_view": create_view, "name": name},
        )
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
                    surface_to_user=False,
                )
            except ValueError as exc:
                return {"error": str(exc)}
            out = ExploreResult(
                columns=result.columns,
                sample_rows=result.rows[: ctx.deps.sample_limit],
                row_count=len(result.rows),
                elapsed_ms=result.elapsed_ms,
                truncated=False,
                saved_query_id=view_id,
                id_count=len(result.rows),
            )
        else:
            out = ExploreResult(
                columns=result.columns,
                sample_rows=result.rows[: ctx.deps.sample_limit],
                row_count=result.row_count if result.row_count >= 0 else len(result.rows),
                elapsed_ms=result.elapsed_ms,
                truncated=result.row_count < 0,
            )
        trace_tool(ctx, "tool_result", {"tool": "sql_explore", "result": out.model_dump(mode="json")})
        return out.model_dump(mode="json")

    @toolset.tool
    async def sql_deliver(
        ctx: RunContext[AgentDeps],
        sql: str,
        entity: SavedQueryEntity | None = None,
        name: str | None = None,
        allow_slow: bool = False,
    ) -> dict[str, Any]:
        """Deliver a final result set for humans. Creates a saved_query.

        entity=metadata|actor: SQL MUST return a column named `id`; the preset becomes a
        filter in /meta or /actors AND can be opened as a data table.
        Omit entity (or use data): any read-only result, rendered only as a data table.
        """
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "sql_deliver", "sql": sql, "entity": entity, "name": name, "allow_slow": allow_slow},
        )
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
            view_id, display_name, entity_ids = await materialize_saved_query(
                ctx.deps,
                sql=sql,
                entity=entity,
                name=name,
                result=result,
                surface_to_user=True,
            )
        except ValueError as exc:
            return {"error": str(exc)}

        out = DeliverResult(
            saved_query_id=view_id,
            name=display_name,
            entity=entity or SavedQueryEntity.DATA,
            id_count=len(entity_ids) or len(result.rows),
            columns=result.columns,
            sample_rows=result.rows[: ctx.deps.sample_limit],
            elapsed_ms=result.elapsed_ms,
        )
        trace_tool(ctx, "tool_result", {"tool": "sql_deliver", "result": out.model_dump(mode="json")})
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
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "inspect_result", "saved_query_id": saved_query_id, "offset": offset, "limit": limit},
        )
        query = await ctx.deps.repo.get_saved_query(saved_query_id)
        if query is None:
            return {"error": f"saved_query {saved_query_id} 不存在"}
        try:
            cached = await ctx.deps.executor.ensure_cached(query, timeout_ms=ctx.deps.sql_timeout_ms)
        except (SqlSandboxError, SqlTimeoutError) as exc:
            return {"error": str(exc)}

        out = InspectResult(
            saved_query_id=saved_query_id,
            columns=cached.columns,
            rows=cached.rows[offset : offset + limit],
            offset=offset,
            limit=limit,
            total_ids=len(cached.rows),
        )
        trace_tool(ctx, "tool_result", {"tool": "inspect_result", "result": out.model_dump(mode="json")})
        return out.model_dump(mode="json")

    return toolset
