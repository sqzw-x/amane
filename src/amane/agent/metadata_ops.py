"""metadata-ops Capability - 渐进披露的元数据写面工具."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from amane.aggregate import RAW_TO_DB_FIELD, SCALAR_FIELD_NAMES
from amane.db.models import TaskType
from amane.db.repo_types import MetadataFields
from amane.handlers.models import CacheKind, ScrapePayload
from amane.parsing import ContentType

from .tools import AgentDeps, require_approval, trace_tool

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


def _compute_merge_updates(
    raw: dict[str, dict[str, object]], field_sources: dict[str, str], selections: dict[str, str]
) -> dict[str, object]:
    """按 selections (字段 -> 来源) 从 raw 数据提取值构造合并更新. 重命名字段以 {source: value} 保留来源, 标量字段来源并入 field_sources."""
    updates: dict[str, object] = {}
    field_sources_updates: dict[str, str] = {}
    for field, source in selections.items():
        if source not in raw:
            raise ValueError(f"source '{source}' not found in raw data")
        if field not in raw[source]:
            raise ValueError(f"field '{field}' not found for source '{source}'")
        value = raw[source][field]
        if value is None:
            continue
        db_field = RAW_TO_DB_FIELD.get(field, field)
        updates[db_field] = {source: value} if db_field != field else value
        if field in SCALAR_FIELD_NAMES:
            field_sources_updates[field] = source
    if field_sources_updates:
        updates["field_sources"] = {**field_sources, **field_sources_updates}
    return updates


def build_metadata_ops_capability() -> Capability[AgentDeps]:
    """按需加载的元数据管理能力."""
    cap: Capability[AgentDeps] = Capability(
        id="metadata-ops",
        description=(
            "Use for editing metadata fields, user tags, merge from raw sources, "
            "enqueue scrape tasks, or deleting metadata. Load before write tools."
        ),
        instructions=(
            "You may mutate metadata only via these tools — never raw SQL writes. "
            "Prefer ids from sql_deliver / explore views. "
            "delete_metadata and batch_delete_metadata require user approval."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def update_metadata(ctx: RunContext[AgentDeps], metadata_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        """Patch metadata fields (title, tags, plot, urls, ...). Omits id/number/raw."""
        trace_tool(ctx, "tool_call", {"tool": "update_metadata", "metadata_id": metadata_id, "patch": patch})
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _AGENT_PATCH_KEYS)
        if unknown:
            return {"error": f"不允许的字段: {', '.join(unknown)}"}
        row = await ctx.deps.repo.update_metadata(metadata_id, **cast(MetadataFields, patch))
        if row is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        out = {"id": row.id, "number": row.number, "title": row.title, "updated": True}
        trace_tool(ctx, "tool_result", {"tool": "update_metadata", "result": out})
        return out

    @cap.tool
    async def attach_user_tag(ctx: RunContext[AgentDeps], metadata_id: int, user_tag_id: int) -> dict[str, Any]:
        """Attach a user tag to one metadata row."""
        trace_tool(
            ctx, "tool_call", {"tool": "attach_user_tag", "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        )
        ok = await ctx.deps.repo.attach_user_tag(metadata_id, user_tag_id)
        out = {"ok": ok, "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        if not ok:
            out["error"] = "挂载失败 (元数据或标签不存在, 或已挂载)"
        trace_tool(ctx, "tool_result", {"tool": "attach_user_tag", "result": out})
        return out

    @cap.tool
    async def detach_user_tag(ctx: RunContext[AgentDeps], metadata_id: int, user_tag_id: int) -> dict[str, Any]:
        """Detach a user tag from one metadata row."""
        trace_tool(
            ctx, "tool_call", {"tool": "detach_user_tag", "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        )
        ok = await ctx.deps.repo.detach_user_tag(metadata_id, user_tag_id)
        out = {"ok": ok, "metadata_id": metadata_id, "user_tag_id": user_tag_id}
        if not ok:
            out["error"] = "取消挂载失败 (关联不存在)"
        trace_tool(ctx, "tool_result", {"tool": "detach_user_tag", "result": out})
        return out

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
        out = {"action": action, "affected": affected, "missing": missing}
        trace_tool(ctx, "tool_result", {"tool": "batch_user_tags", "result": out})
        return out

    @cap.tool
    async def merge_metadata(
        ctx: RunContext[AgentDeps], metadata_id: int, selections: dict[str, str]
    ) -> dict[str, Any]:
        """Merge fields from raw sources: selections maps field_name -> source_key."""
        trace_tool(ctx, "tool_call", {"tool": "merge_metadata", "metadata_id": metadata_id, "selections": selections})
        if not selections:
            return {"error": "selections 为空"}
        metadata = await ctx.deps.repo.get_metadata(metadata_id)
        if metadata is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        try:
            updates = _compute_merge_updates(metadata.raw, metadata.field_sources, selections)
        except ValueError as exc:
            return {"error": str(exc)}
        if not updates:
            return {"error": "无有效合并项"}
        updated = await ctx.deps.repo.update_metadata(metadata_id, **cast(MetadataFields, updates))
        assert updated is not None
        out = {"id": updated.id, "number": updated.number, "merged_fields": list(selections)}
        trace_tool(ctx, "tool_result", {"tool": "merge_metadata", "result": out})
        return out

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
        task_ids: list[int] = []
        missing = 0
        for metadata_id in metadata_ids:
            metadata = await ctx.deps.repo.get_metadata(metadata_id)
            if metadata is None:
                missing += 1
                continue
            payload = ScrapePayload(number=metadata.number, content_type=content_type, use_cache=cache_kinds)
            task = await ctx.deps.repo.create_task(task_type=TaskType.SCRAPE, payload=payload)
            assert task.id is not None
            task_ids.append(task.id)
        out = {"submitted": len(task_ids), "missing": missing, "task_ids": task_ids}
        trace_tool(ctx, "tool_result", {"tool": "enqueue_scrape", "result": out})
        return out

    @cap.tool
    async def delete_metadata(ctx: RunContext[AgentDeps], metadata_id: int) -> dict[str, Any]:
        """Delete one metadata row. Requires user approval."""
        detail = f"删除元数据 id={metadata_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_metadata", "metadata_id": metadata_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_metadata",
            extra={"metadata_id": metadata_id},
        )
        ok = await ctx.deps.repo.delete_metadata(metadata_id)
        out = {"tool": "delete_metadata", "metadata_id": metadata_id, "deleted": ok}
        if not ok:
            out["error"] = f"metadata {metadata_id} 不存在"
        trace_tool(ctx, "tool_result", {"tool": "delete_metadata", "result": out})
        return out

    @cap.tool
    async def batch_delete_metadata(ctx: RunContext[AgentDeps], metadata_ids: list[int]) -> dict[str, Any]:
        """Delete many metadata rows. Requires user approval."""
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
        out = {
            "tool": "batch_delete_metadata",
            "deleted": deleted,
            "missing": missing,
            "metadata_ids": list(metadata_ids),
        }
        trace_tool(ctx, "tool_result", {"tool": "batch_delete_metadata", "result": out})
        return out

    return cap
