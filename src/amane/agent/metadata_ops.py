from __future__ import annotations

from typing import Any, Literal, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..aggregate import compute_merge_updates
from ..db.models import TaskType
from ..db.repo_types import MetadataFields
from ..handlers.models import CacheKind, ScrapePayload
from ..parsing import ContentType
from .tools import TOOL_OK, AgentDeps, require_approval, trace_tool, unknown_field_error

_AGENT_PATCH_KEYS = frozenset(
    {
        "title",
        "actors",
        "studio",
        "publisher",
        "release",
        "runtime",
        "tags",
        "series",
        "plot",
        "directors",
        "poster_urls",
        "thumb_urls",
        "trailer_urls",
        "extrafanart_urls",
        "scores",
        "external_ids",
        "source_urls",
    }
)


def build_metadata_ops_capability() -> Capability[AgentDeps]:
    cap: Capability[AgentDeps] = Capability(
        id="metadata-ops",
    )

    @cap.tool
    async def update_metadata(
        ctx: RunContext[AgentDeps], metadata_id: int, patch: dict[str, Any]
    ) -> str | dict[str, Any]:
        """Patch metadata fields (title, tags, plot, urls, ...). Omits id/number/raw/field_sources."""
        trace_tool(ctx, "tool_call", {"tool": "update_metadata", "metadata_id": metadata_id, "patch": patch})
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _AGENT_PATCH_KEYS)
        if unknown:
            return unknown_field_error(unknown, _AGENT_PATCH_KEYS)
        row = await ctx.deps.repo.update_metadata(metadata_id, **cast(MetadataFields, patch))
        if row is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "update_metadata", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def attach_user_tag(ctx: RunContext[AgentDeps], metadata_id: int, user_tag_id: int) -> str | dict[str, Any]:
        """Attach a user tag to one metadata row (already attached → OK)."""
        trace_tool(
            ctx, "tool_call", {"tool": "attach_user_tag", "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        )
        if await ctx.deps.repo.get_metadata(metadata_id) is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        if await ctx.deps.repo.get_user_tag(user_tag_id) is None:
            return {"error": f"user_tag {user_tag_id} 不存在"}
        if not await ctx.deps.repo.attach_user_tag(metadata_id, user_tag_id):
            return {"error": f"挂载失败: metadata {metadata_id} / user_tag {user_tag_id}"}
        trace_tool(ctx, "tool_result", {"tool": "attach_user_tag", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def detach_user_tag(ctx: RunContext[AgentDeps], metadata_id: int, user_tag_id: int) -> str | dict[str, Any]:
        """Detach a user tag from one metadata row."""
        trace_tool(
            ctx, "tool_call", {"tool": "detach_user_tag", "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        )
        if not await ctx.deps.repo.detach_user_tag(metadata_id, user_tag_id):
            return {"error": f"metadata {metadata_id} 未挂载 user_tag {user_tag_id}"}
        trace_tool(ctx, "tool_result", {"tool": "detach_user_tag", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def batch_user_tags(
        ctx: RunContext[AgentDeps],
        metadata_ids: list[int],
        user_tag_id: int,
        action: Literal["attach", "detach"] = "attach",
    ) -> dict[str, Any]:
        """Batch attach/detach a user tag on many metadata ids."""
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "batch_user_tags",
                "metadata_ids": metadata_ids,
                "user_tag_id": user_tag_id,
                "action": action,
            },
        )
        if not metadata_ids:
            return {"error": "metadata_ids 为空"}
        if action == "attach":
            affected, missing = await ctx.deps.repo.batch_attach_user_tag(metadata_ids, user_tag_id)
        else:
            affected, missing = await ctx.deps.repo.batch_detach_user_tag(metadata_ids, user_tag_id)
        out = {"affected": affected, "missing": missing}
        trace_tool(ctx, "tool_result", {"tool": "batch_user_tags", "result": out})
        return out

    @cap.tool
    async def merge_metadata(
        ctx: RunContext[AgentDeps], metadata_id: int, selections: dict[str, str]
    ) -> str | dict[str, Any]:
        """Merge fields from raw sources: selections maps field_name -> source_key."""
        trace_tool(ctx, "tool_call", {"tool": "merge_metadata", "metadata_id": metadata_id, "selections": selections})
        if not selections:
            return {"error": "selections 为空"}
        metadata = await ctx.deps.repo.get_metadata(metadata_id)
        if metadata is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        try:
            updates = compute_merge_updates(metadata.raw, metadata.field_sources, selections)
        except ValueError as exc:
            return {"error": str(exc)}
        if not updates:
            return {"error": "无有效合并项"}
        await ctx.deps.repo.update_metadata(metadata_id, **cast(MetadataFields, updates))
        trace_tool(ctx, "tool_result", {"tool": "merge_metadata", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def enqueue_scrape(
        ctx: RunContext[AgentDeps],
        metadata_ids: list[int],
        use_cache: set[CacheKind] | None = None,
        content_type: ContentType = ContentType.CENSORED,
    ) -> dict[str, Any]:
        """Enqueue SCRAPE tasks for metadata ids (by each row's number)."""
        cache_kinds = use_cache if use_cache is not None else {CacheKind.metadata, CacheKind.trans}
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "enqueue_scrape",
                "metadata_ids": metadata_ids,
                "use_cache": sorted(cache_kinds),
                "content_type": content_type,
            },
        )
        if not metadata_ids:
            return {"error": "metadata_ids 为空"}
        submitted = 0
        missing = 0
        for metadata_id in metadata_ids:
            metadata = await ctx.deps.repo.get_metadata(metadata_id)
            if metadata is None:
                missing += 1
                continue
            payload = ScrapePayload(number=metadata.number, content_type=content_type, use_cache=cache_kinds)
            await ctx.deps.repo.create_task(task_type=TaskType.SCRAPE, payload=payload)
            submitted += 1
        out = {"submitted": submitted, "missing": missing}
        trace_tool(ctx, "tool_result", {"tool": "enqueue_scrape", "result": out})
        return out

    @cap.tool
    async def delete_metadata(ctx: RunContext[AgentDeps], metadata_id: int) -> str | dict[str, Any]:
        """Delete one metadata row."""
        detail = f"删除元数据 id={metadata_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_metadata", "metadata_id": metadata_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_metadata",
            extra={"metadata_id": metadata_id},
        )
        if not await ctx.deps.repo.delete_metadata(metadata_id):
            return {"error": f"metadata {metadata_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "delete_metadata", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def batch_delete_metadata(ctx: RunContext[AgentDeps], metadata_ids: list[int]) -> dict[str, Any]:
        """Delete many metadata rows."""
        if not metadata_ids:
            return {"error": "metadata_ids 为空"}
        detail = f"批量删除元数据 ids={metadata_ids}"
        trace_tool(ctx, "tool_call", {"tool": "batch_delete_metadata", "metadata_ids": metadata_ids})
        require_approval(
            ctx,
            sql=detail,
            tool="batch_delete_metadata",
            extra={"metadata_ids": list(metadata_ids)},
        )
        deleted, missing = await ctx.deps.repo.batch_delete_metadata(metadata_ids)
        out = {"deleted": deleted, "missing": missing}
        trace_tool(ctx, "tool_result", {"tool": "batch_delete_metadata", "result": out})
        return out

    return cap
