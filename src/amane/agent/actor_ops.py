from __future__ import annotations

from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..db.models import FacetKind, TaskType
from ..db.repo_types import ActorPersonFields
from ..handlers.models import ActorScrapePayload, CacheKind
from ..utils.dates import normalize_calendar_date
from .tools import TOOL_OK, AgentDeps, trace_tool, unknown_field_error

_AGENT_ACTOR_PATCH_KEYS = frozenset(
    {
        "aliases",
        "gender",
        "birthday",
        "birthplace",
        "height",
        "bust",
        "waist",
        "hip",
        "cup",
        "overview",
        "tagline",
        "image_urls",
        "provider_ids",
        "source_urls",
    }
)


def build_actor_ops_capability() -> Capability[AgentDeps]:
    """身份合并经 facet-identity."""
    cap: Capability[AgentDeps] = Capability(
        id="actor-ops",
        instructions=(
            "Alias rows are one-to-many and one alias may be shared by several actors; when "
            "resolve_actor_name returns several matches, ask the user which one is meant. "
            "The display name is not an alias row — switch it with set_actor_display_name, which is "
            "equivalent to rename_facet with kind=actor; call only one of the two."
        ),
    )

    @cap.tool
    async def get_actor_aliases(ctx: RunContext[AgentDeps], actor_id: int) -> dict[str, Any]:
        """List an actor's display name and alias rows (in order)."""
        trace_tool(ctx, "tool_call", {"tool": "get_actor_aliases", "actor_id": actor_id})
        actor = await ctx.deps.repo.get_actor(actor_id)
        if actor is None:
            return {"error": f"actor {actor_id} 不存在"}
        aliases = await ctx.deps.repo.get_actor_aliases(actor_id)
        out = {"name": actor.name, "aliases": aliases}
        trace_tool(ctx, "tool_result", {"tool": "get_actor_aliases", "result": out})
        return out

    @cap.tool
    async def resolve_actor_name(ctx: RunContext[AgentDeps], name: str) -> dict[str, Any]:
        """Resolve a name to actor candidates (display-name hit first, else alias rows).

        No match → empty list; exactly one → unique; multiple → the alias is shared
        (ambiguous) — ask the user which actor before writing.
        """
        cleaned = (name or "").strip()
        trace_tool(ctx, "tool_call", {"tool": "resolve_actor_name", "name": cleaned})
        if not cleaned:
            return {"error": "名字不能为空"}
        actors = await ctx.deps.repo.lookup_actors_by_name(cleaned)
        matches = [{"id": a.id, "name": a.name, "is_display": a.name == cleaned} for a in actors if a.id is not None]
        out = {"matches": matches}
        trace_tool(ctx, "tool_result", {"tool": "resolve_actor_name", "result": out})
        return out

    @cap.tool
    async def add_actor_alias(ctx: RunContext[AgentDeps], actor_id: int, name: str) -> str | dict[str, Any]:
        """Add one alias row to an actor (idempotent; duplicate → error)."""
        cleaned = (name or "").strip()
        trace_tool(ctx, "tool_call", {"tool": "add_actor_alias", "actor_id": actor_id, "name": cleaned})
        if not cleaned:
            return {"error": "名字不能为空"}
        actor = await ctx.deps.repo.get_actor(actor_id)
        if actor is None:
            return {"error": f"actor {actor_id} 不存在"}
        if cleaned == actor.name:
            return {"error": f"「{cleaned}」是当前展示名; 切换展示名请用 set_actor_display_name"}
        if not await ctx.deps.repo.add_actor_alias(actor_id, cleaned):
            return {"error": f"别名「{cleaned}」已存在"}
        trace_tool(ctx, "tool_result", {"tool": "add_actor_alias", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def remove_actor_alias(ctx: RunContext[AgentDeps], actor_id: int, name: str) -> str | dict[str, Any]:
        """Remove one alias row from an actor (missing → error).

        Display names are not alias rows and cannot be removed here — use
        set_actor_display_name to switch, or rename_facet for a new name.
        """
        cleaned = (name or "").strip()
        trace_tool(ctx, "tool_call", {"tool": "remove_actor_alias", "actor_id": actor_id, "name": cleaned})
        if not cleaned:
            return {"error": "名字不能为空"}
        actor = await ctx.deps.repo.get_actor(actor_id)
        if actor is None:
            return {"error": f"actor {actor_id} 不存在"}
        if cleaned == actor.name:
            return {"error": f"「{cleaned}」是当前展示名, 不能作为别名删除; 切换展示名用 set_actor_display_name"}
        if not await ctx.deps.repo.remove_actor_alias(actor_id, cleaned):
            return {"error": f"别名「{cleaned}」不存在"}
        trace_tool(ctx, "tool_result", {"tool": "remove_actor_alias", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def set_actor_display_name(ctx: RunContext[AgentDeps], actor_id: int, name: str) -> str | dict[str, Any]:
        """Switch the actor's display name (old name becomes an alias row).

        The name may be an existing alias or a new one; related metadata actor names are
        rewritten. Fails with a conflict error if another actor already uses the name —
        merge those actors first instead.
        """
        cleaned = (name or "").strip()
        trace_tool(ctx, "tool_call", {"tool": "set_actor_display_name", "actor_id": actor_id, "name": cleaned})
        if not cleaned:
            return {"error": "名字不能为空"}
        try:
            item = await ctx.deps.repo.rename_facet(FacetKind.ACTOR, actor_id, cleaned)
        except ValueError as exc:
            return {"error": str(exc)}
        if item is None:
            return {"error": f"actor {actor_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "set_actor_display_name", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def update_actor(ctx: RunContext[AgentDeps], actor_id: int, patch: dict[str, Any]) -> str | dict[str, Any]:
        """Patch actor person fields (gender, birthday, ...; aliases replaces the whole list).

        Prefer add_actor_alias / remove_actor_alias for surgical alias edits — the aliases
        key here replaces all alias rows. Omits name/raw/field_sources.
        """
        trace_tool(ctx, "tool_call", {"tool": "update_actor", "actor_id": actor_id, "patch": patch})
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _AGENT_ACTOR_PATCH_KEYS)
        if unknown:
            return unknown_field_error(unknown, _AGENT_ACTOR_PATCH_KEYS)
        updates = dict(patch)
        if "birthday" in updates:
            raw_bday = updates["birthday"]
            if raw_bday is None or (isinstance(raw_bday, str) and not raw_bday.strip()):
                updates["birthday"] = None
            elif isinstance(raw_bday, str):
                normalized = normalize_calendar_date(raw_bday)
                if normalized is None:
                    return {"error": "birthday 须为 YYYY-MM-DD"}
                updates["birthday"] = normalized
            else:
                return {"error": "birthday 须为 YYYY-MM-DD"}
        actor = await ctx.deps.repo.update_actor(actor_id, **cast(ActorPersonFields, updates))
        if actor is None:
            return {"error": f"actor {actor_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "update_actor", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def enqueue_actor_scrape(
        ctx: RunContext[AgentDeps], actor_ids: list[int], use_cache: set[CacheKind] | None = None
    ) -> dict[str, Any]:
        """Enqueue ACTOR_SCRAPE tasks for actor ids."""
        cache_kinds = use_cache if use_cache is not None else {CacheKind.metadata, CacheKind.trans}
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "enqueue_actor_scrape",
                "actor_ids": actor_ids,
                "use_cache": sorted(cache_kinds),
            },
        )
        if not actor_ids:
            return {"error": "actor_ids 为空"}
        submitted = 0
        missing = 0
        for actor_id in actor_ids:
            actor = await ctx.deps.repo.get_actor(actor_id)
            if actor is None:
                missing += 1
                continue
            await ctx.deps.repo.create_task(
                task_type=TaskType.ACTOR_SCRAPE, payload=ActorScrapePayload(actor_id=actor_id, use_cache=cache_kinds)
            )
            submitted += 1
        out = {"submitted": submitted, "missing": missing}
        trace_tool(ctx, "tool_result", {"tool": "enqueue_actor_scrape", "result": out})
        return out

    return cap
