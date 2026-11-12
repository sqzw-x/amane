"""查询预设的改名与清理.

预设由 ``sql_deliver`` 产出; 外部派发交付的预设直接已保留, 与会话解耦, 删除是唯一的清理手段.
SQL 不改写: 改 SQL 要先过只读沙箱校验, 那条路径留在 REST (见 ``api/routes/saved_queries.py``).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.models.saved_queries import (
    SAVED_QUERY_DESCRIPTION_MAX,
    SAVED_QUERY_IDS_MAX,
    SAVED_QUERY_NAME_MAX,
)
from ..db.repo_types import SavedQueryUpdates
from .tools import TOOL_OK, AgentDeps, require_approval


def build_saved_query_ops_capability() -> Capability[AgentDeps]:
    """删除须批准."""
    cap: Capability[AgentDeps] = Capability(id="saved-query-ops")

    @cap.tool
    async def update_saved_query(
        ctx: RunContext[AgentDeps],
        query_id: int,
        name: str | None = None,
        description: str | None = None,
    ) -> str | dict[str, Any]:
        """Rename a preset or edit its description. SQL stays unchanged."""
        updates: SavedQueryUpdates = {}
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                return {"error": "名称不能为空"}
            if len(cleaned) > SAVED_QUERY_NAME_MAX:
                return {"error": f"名称最多 {SAVED_QUERY_NAME_MAX} 个字符"}
            updates["name"] = cleaned
        if description is not None:
            cleaned_description = description.strip()
            if len(cleaned_description) > SAVED_QUERY_DESCRIPTION_MAX:
                return {"error": f"描述最多 {SAVED_QUERY_DESCRIPTION_MAX} 个字符"}
            updates["description"] = cleaned_description
        if not updates:
            return {"error": "name 与 description 至少给一个"}
        if await ctx.deps.repo.update_saved_query(query_id, **updates) is None:
            return {"error": f"查询预设 {query_id} 不存在"}
        return TOOL_OK

    @cap.tool
    async def delete_saved_queries(ctx: RunContext[AgentDeps], ids: list[int]) -> dict[str, Any]:
        """Delete presets by id. Not undoable."""
        if not ids:
            return {"error": "ids 为空"}
        if len(ids) > SAVED_QUERY_IDS_MAX:
            return {"error": f"一次最多删除 {SAVED_QUERY_IDS_MAX} 个预设"}
        detail = f"删除查询预设 ids={list(ids)}"
        require_approval(
            ctx,
            sql=detail,
            tool="delete_saved_queries",
            extra={"ids": list(ids)},
        )
        deleted, missing = await ctx.deps.repo.delete_saved_queries(list(ids))
        # 与 REST 一致: 对请求中的全部 id 失效, 未命中的键是 no-op.
        for query_id in ids:
            ctx.deps.executor.cache.invalidate(query_id)
        return {"deleted": deleted, "missing": missing}

    return cap
