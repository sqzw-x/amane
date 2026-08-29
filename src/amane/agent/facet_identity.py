"""facet-identity Capability - 分类身份治理 (rename / merge / delete / rules)."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from amane.db.models import SCRAPE_FACET_KINDS, FacetKind

from .tools import AgentDeps, require_approval, trace_tool


def build_facet_identity_capability() -> Capability[AgentDeps]:
    """按需加载的分类身份治理; 合并/删除/删规则须用户批准."""
    cap: Capability[AgentDeps] = Capability(
        id="facet-identity",
        description=(
            "Use for facet rename, merge, delete, and scrape-side rule listing/deletion. "
            "Load before identity governance tools."
        ),
        instructions=(
            "Facet kinds: actor, director, tag, studio, publisher, series, user_tag. "
            "Rename fails with conflict if another facet already has the name — use merge instead. "
            "For actor kind, rename_facet switches the display name (old name becomes an alias row); "
            "alias rows themselves belong in actor-ops. "
            "Deleting a scrape-side facet writes a block rule and strips names from metadata. "
            "merge_facets / delete_facet / delete_facet_rule require user approval. "
            "Actor person field edits belong in actor-ops, not here."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def rename_facet(ctx: RunContext[AgentDeps], kind: FacetKind, facet_id: int, name: str) -> dict[str, Any]:
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
        out = {"id": item.id, "kind": kind, "name": item.name, "count": item.count}
        trace_tool(ctx, "tool_result", {"tool": "rename_facet", "result": out})
        return out

    @cap.tool
    async def merge_facets(
        ctx: RunContext[AgentDeps], kind: FacetKind, target_id: int, source_ids: list[int]
    ) -> dict[str, Any]:
        """Merge source facet ids into target; sources are deleted. Requires approval."""
        if not source_ids:
            return {"error": "source_ids 为空"}
        detail = f"合并 {kind}: sources={source_ids} → target={target_id}"
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "merge_facets", "kind": kind, "target_id": target_id, "source_ids": source_ids},
        )
        require_approval(
            ctx,
            sql=detail,
            tool="merge_facets",
            extra={"kind": kind, "target_id": target_id, "source_ids": list(source_ids)},
        )
        item = await ctx.deps.repo.merge_facets(kind, target_id, source_ids)
        if item is None:
            return {"error": "目标分类不存在", "tool": "merge_facets"}
        out = {
            "tool": "merge_facets",
            "kind": kind,
            "id": item.id,
            "name": item.name,
            "count": item.count,
            "source_ids": list(source_ids),
        }
        trace_tool(ctx, "tool_result", {"tool": "merge_facets", "result": out})
        return out

    @cap.tool
    async def delete_facet(ctx: RunContext[AgentDeps], kind: FacetKind, facet_id: int) -> dict[str, Any]:
        """Delete a facet (scrape kinds → block rule). Requires approval."""
        detail = f"删除分类 {kind} id={facet_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_facet", "kind": kind, "facet_id": facet_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_facet",
            extra={"kind": kind, "facet_id": facet_id},
        )
        ok = await ctx.deps.repo.delete_facet(kind, facet_id)
        out: dict[str, Any] = {
            "tool": "delete_facet",
            "kind": kind,
            "facet_id": facet_id,
            "deleted": ok,
        }
        if not ok:
            out["error"] = f"{kind} {facet_id} 不存在"
        trace_tool(ctx, "tool_result", {"tool": "delete_facet", "result": out})
        return out

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
                "kind": str(r.kind),
                "source_name": r.source_name,
                "action": str(r.action),
                "target_name": r.target_name,
            }
            for r in rules
        ]
        out = {"kind": kind, "items": items, "total": len(items)}
        trace_tool(ctx, "tool_result", {"tool": "list_facet_rules", "result": {"total": out["total"]}})
        return out

    @cap.tool
    async def delete_facet_rule(ctx: RunContext[AgentDeps], kind: FacetKind, rule_id: int) -> dict[str, Any]:
        """Delete one facet rule (does not backfill metadata). Requires approval."""
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
        ok = await ctx.deps.repo.delete_facet_rule(kind, rule_id)
        out: dict[str, Any] = {
            "tool": "delete_facet_rule",
            "kind": kind,
            "rule_id": rule_id,
            "deleted": ok,
        }
        if not ok:
            out["error"] = f"规则 {rule_id} 不存在"
        trace_tool(ctx, "tool_result", {"tool": "delete_facet_rule", "result": out})
        return out

    return cap
