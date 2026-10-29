from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..db.models import SCRAPE_FACET_KINDS, FacetKind
from .tools import TOOL_OK, AgentDeps, require_approval


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
        """Rename a facet. Conflicting name → error (use merge_facets).

        Only the display name changes; links stay. Reversible by renaming back.
        """
        cleaned = name.strip()
        if not cleaned:
            return {"error": "名称不能为空"}
        try:
            item = await ctx.deps.repo.rename_facet(kind, facet_id, cleaned)
        except ValueError as exc:
            return {"error": str(exc)}
        if item is None:
            return {"error": f"{kind} {facet_id} 不存在"}
        return TOOL_OK

    @cap.tool
    async def merge_facets(
        ctx: RunContext[AgentDeps], kind: FacetKind, target_id: int, source_ids: list[int]
    ) -> str | dict[str, Any]:
        """Merge source facet ids into target; sources are deleted.

        Links move to the target and the source entries disappear, so this is not undoable.
        Scrape kinds also record the merged names as aliases.
        """
        if not source_ids:
            return {"error": "source_ids 为空"}
        detail = f"合并 {kind}: sources={source_ids} → target={target_id}"
        require_approval(
            ctx,
            sql=detail,
            tool="merge_facets",
            extra={"kind": kind, "target_id": target_id, "source_ids": list(source_ids)},
        )
        if await ctx.deps.repo.merge_facets(kind, target_id, source_ids) is None:
            return {"error": "目标分类不存在"}
        return TOOL_OK

    @cap.tool
    async def ensure_user_tags(ctx: RunContext[AgentDeps], names: list[str]) -> dict[str, Any]:
        """Get or create user tags by name, returning ids in input order.

        Existing names are reused; `created` counts the new ones.
        """
        cleaned = list(dict.fromkeys(name.strip() for name in names if name.strip()))
        if not cleaned:
            return {"error": "names 为空或全为空白"}
        tags, created = await ctx.deps.repo.ensure_user_tags(cleaned)
        return {
            "tags": [{"id": tag.id, "name": tag.name} for tag in tags],
            "created": created,
        }

    @cap.tool
    async def delete_facet(ctx: RunContext[AgentDeps], kind: FacetKind, facet_id: int) -> str | dict[str, Any]:
        """Delete a facet (scrape kinds → block rule).

        Scrape kinds keep blocking future scrapes under that name; user tags drop their links.
        Not undoable.
        """
        detail = f"删除分类 {kind} id={facet_id}"
        require_approval(
            ctx,
            sql=detail,
            tool="delete_facet",
            extra={"kind": kind, "facet_id": facet_id},
        )
        if not await ctx.deps.repo.delete_facet(kind, facet_id):
            return {"error": f"{kind} {facet_id} 不存在"}
        return TOOL_OK

    @cap.tool
    async def list_facet_rules(ctx: RunContext[AgentDeps], kind: FacetKind) -> dict[str, Any]:
        """List alias/block rules for a scrape-side facet kind."""
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
        return {"items": items}

    @cap.tool
    async def delete_facet_rule(ctx: RunContext[AgentDeps], kind: FacetKind, rule_id: int) -> str | dict[str, Any]:
        """Delete one facet rule (does not backfill metadata).

        Rows already matched by the rule keep their current values; only future scrapes change.
        """
        if kind not in SCRAPE_FACET_KINDS:
            return {"error": "该分类不支持规则"}
        detail = f"删除分类规则 {kind} rule_id={rule_id}"
        require_approval(
            ctx,
            sql=detail,
            tool="delete_facet_rule",
            extra={"kind": kind, "rule_id": rule_id},
        )
        if not await ctx.deps.repo.delete_facet_rule(kind, rule_id):
            return {"error": f"规则 {rule_id} 不存在"}
        return TOOL_OK

    return cap
