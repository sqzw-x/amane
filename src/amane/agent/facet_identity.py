from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..db.models import SCRAPE_FACET_KINDS, FacetKind
from .tools import TOOL_OK, AgentDeps, require_approval, trace_tool


def build_facet_identity_capability() -> Capability[AgentDeps]:
    """合并 / 删除 / 删规则须用户批准."""
    cap: Capability[AgentDeps] = Capability(
        id="facet-identity",
        instructions=(
            "Rename fails when another facet already holds the name — merge_facets is how two "
            "facets are combined. Deleting a scrape-side facet writes a block rule and removes "
            "that name from metadata."
        ),
    )

    @cap.tool
    async def rename_facet(
        ctx: RunContext[AgentDeps], kind: FacetKind, facet_id: int, name: str
    ) -> str | dict[str, Any]:
        """Rename a facet. Conflicting name → error (use merge_facets)."""
        cleaned = name.strip()
        trace_tool(ctx, "tool_call", {"tool": "rename_facet", "kind": kind, "facet_id": facet_id, "name": cleaned})
        if not cleaned:
            return {"error": "名称不能为空"}
        try:
            item = await ctx.deps.repo.rename_facet(kind, facet_id, cleaned)
        except ValueError as exc:
            return {"error": str(exc)}
        if item is None:
            return {"error": f"{kind} {facet_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "rename_facet", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def merge_facets(
        ctx: RunContext[AgentDeps], kind: FacetKind, target_id: int, source_ids: list[int]
    ) -> str | dict[str, Any]:
        """Merge source facet ids into target; sources are deleted."""
        if not source_ids:
            return {"error": "source_ids 为空"}
        detail = f"合并 {kind}: sources={source_ids} → target={target_id}"
        trace_tool(
            ctx, "tool_call", {"tool": "merge_facets", "kind": kind, "target_id": target_id, "source_ids": source_ids}
        )
        require_approval(
            ctx,
            sql=detail,
            tool="merge_facets",
            extra={"kind": kind, "target_id": target_id, "source_ids": list(source_ids)},
        )
        if await ctx.deps.repo.merge_facets(kind, target_id, source_ids) is None:
            return {"error": "目标分类不存在"}
        trace_tool(ctx, "tool_result", {"tool": "merge_facets", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def delete_facet(ctx: RunContext[AgentDeps], kind: FacetKind, facet_id: int) -> str | dict[str, Any]:
        """Delete a facet (scrape kinds → block rule)."""
        detail = f"删除分类 {kind} id={facet_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_facet", "kind": kind, "facet_id": facet_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_facet",
            extra={"kind": kind, "facet_id": facet_id},
        )
        if not await ctx.deps.repo.delete_facet(kind, facet_id):
            return {"error": f"{kind} {facet_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "delete_facet", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def list_facet_rules(ctx: RunContext[AgentDeps], kind: FacetKind) -> dict[str, Any]:
        """List alias/block rules for a scrape-side facet kind."""
        trace_tool(ctx, "tool_call", {"tool": "list_facet_rules", "kind": kind})
        if kind not in SCRAPE_FACET_KINDS:
            return {"error": "该分类不支持规则"}
        try:
            rules = await ctx.deps.repo.list_facet_rules(kind)
        except ValueError as exc:
            return {"error": str(exc)}
        items = [
            {
                "id": r.id,
                "source_name": r.source_name,
                "action": str(r.action),
                "target_name": r.target_name,
            }
            for r in rules
        ]
        out = {"items": items}
        trace_tool(ctx, "tool_result", {"tool": "list_facet_rules", "result": {"total": len(items)}})
        return out

    @cap.tool
    async def delete_facet_rule(ctx: RunContext[AgentDeps], kind: FacetKind, rule_id: int) -> str | dict[str, Any]:
        """Delete one facet rule (does not backfill metadata)."""
        if kind not in SCRAPE_FACET_KINDS:
            return {"error": "该分类不支持规则"}
        detail = f"删除分类规则 {kind} rule_id={rule_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_facet_rule", "kind": kind, "rule_id": rule_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_facet_rule",
            extra={"kind": kind, "rule_id": rule_id},
        )
        if not await ctx.deps.repo.delete_facet_rule(kind, rule_id):
            return {"error": f"规则 {rule_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "delete_facet_rule", "result": TOOL_OK})
        return TOOL_OK

    return cap
