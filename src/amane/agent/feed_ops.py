"""feed-ops Capability - RSS/Atom 订阅源与历史条目管理."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from sqlalchemy.exc import IntegrityError

from amane.api.models.feeds import (
    FeedItemBatchAction,
    _validate_http_url,
    _validate_number_pattern,
    normalize_feed_group,
)
from amane.db.models import Feed, FeedItem, FeedItemState, TaskType
from amane.db.repo_types import FeedUpdates
from amane.handlers.models import CacheKind, ScrapePayload, build_feed_scrape_payload
from amane.parsing import ContentType

from .tools import AgentDeps, require_approval, trace_tool

_CACHE_KIND_ORDER = (CacheKind.metadata, CacheKind.trans)


class AgentFeedCreate(BaseModel):
    """Agent 可创建的 Feed 字段."""

    name: str = ""
    url: str
    group: str = ""
    enabled: bool = True
    auto_enqueue: bool = True
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    number_pattern: str | None = None
    content_type: ContentType | None = None
    use_cache: set[CacheKind] = Field(default_factory=lambda: {CacheKind.metadata, CacheKind.trans})

    model_config = {"str_strip_whitespace": True}


class AgentFeedUpdate(BaseModel):
    """Agent 可更新的 Feed 字段; 只处理显式传入的字段."""

    name: str | None = None
    url: str | None = None
    group: str | None = None
    enabled: bool | None = None
    auto_enqueue: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    number_pattern: str | None = None
    content_type: ContentType | None = None
    use_cache: set[CacheKind] | None = None


class AgentFeedItemBatch(BaseModel):
    """Feed 历史条目批量操作."""

    action: FeedItemBatchAction
    ids: list[int] = Field(min_length=1)


class FeedInfo(BaseModel):
    id: int
    name: str
    url: str
    group: str
    enabled: bool
    auto_enqueue: bool
    interval_seconds: int
    number_pattern: str | None
    content_type: ContentType | None
    use_cache: list[CacheKind]
    next_fetch_at: datetime | None
    last_fetched_at: datetime | None
    last_error: str | None
    last_enqueued: int


class FeedItemInfo(BaseModel):
    id: int
    feed_id: int
    item_key: str
    title: str | None
    link: str | None
    description: str | None
    number: str | None
    published_at: datetime | None
    created_at: datetime
    ignored_at: datetime | None
    metadata_id: int | None


class FeedListResult(BaseModel):
    items: list[FeedInfo]
    total: int


class FeedItemListResult(BaseModel):
    items: list[FeedItemInfo]
    total: int
    offset: int
    limit: int


class FeedBatchResult(BaseModel):
    action: FeedItemBatchAction
    affected: int = 0
    missing: int = 0
    skipped: int = 0
    submitted: int = 0
    task_ids: list[int] = Field(default_factory=list)


def _cache_kinds(raw: list[str]) -> list[CacheKind]:
    return [kind for kind in _CACHE_KIND_ORDER if kind in raw]


def _feed_info(feed: Feed) -> FeedInfo:
    assert feed.id is not None
    return FeedInfo(
        id=feed.id,
        name=feed.name,
        url=feed.url,
        group=feed.group,
        enabled=feed.enabled,
        auto_enqueue=feed.auto_enqueue,
        interval_seconds=feed.interval_seconds,
        number_pattern=feed.number_pattern,
        content_type=feed.content_type,
        use_cache=_cache_kinds(feed.use_cache),
        next_fetch_at=_as_utc(feed.next_fetch_at),
        last_fetched_at=_as_utc(feed.last_fetched_at),
        last_error=feed.last_error,
        last_enqueued=feed.last_enqueued,
    )


def _feed_item_info(item: FeedItem, metadata_id: int | None) -> FeedItemInfo:
    assert item.id is not None
    return FeedItemInfo(
        id=item.id,
        feed_id=item.feed_id,
        item_key=item.item_key,
        title=item.title,
        link=item.link,
        description=item.description,
        number=item.number,
        published_at=_as_utc(item.published_at),
        created_at=_as_utc(item.created_at) or datetime.now(UTC),
        ignored_at=_as_utc(item.ignored_at),
        metadata_id=metadata_id,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _use_cache_values(use_cache: set[CacheKind]) -> list[str]:
    return [kind for kind in _CACHE_KIND_ORDER if kind in use_cache]


def _feed_create_values(req: AgentFeedCreate) -> tuple[str, str, str, str | None, list[str]]:
    return (
        req.name.strip(),
        _validate_http_url(req.url),
        normalize_feed_group(req.group),
        _validate_number_pattern(req.number_pattern),
        _use_cache_values(req.use_cache),
    )


def _feed_update_values(req: AgentFeedUpdate) -> dict[str, object]:
    fields = req.model_fields_set
    if not fields:
        raise ValueError("patch 为空")

    updates: dict[str, object] = {}
    if "name" in fields:
        if req.name is None:
            raise ValueError("name 不能为 null")
        updates["name"] = req.name.strip()
    if "url" in fields:
        if req.url is None:
            raise ValueError("url 不能为 null")
        updates["url"] = _validate_http_url(req.url)
    if "group" in fields:
        updates["group"] = normalize_feed_group(req.group)
    if "enabled" in fields:
        if req.enabled is None:
            raise ValueError("enabled 不能为 null")
        updates["enabled"] = req.enabled
    if "auto_enqueue" in fields:
        if req.auto_enqueue is None:
            raise ValueError("auto_enqueue 不能为 null")
        updates["auto_enqueue"] = req.auto_enqueue
    if "interval_seconds" in fields:
        if req.interval_seconds is None:
            raise ValueError("interval_seconds 不能为 null")
        updates["interval_seconds"] = req.interval_seconds
    if "number_pattern" in fields:
        updates["number_pattern"] = _validate_number_pattern(req.number_pattern)
    if "content_type" in fields:
        updates["content_type"] = req.content_type
    if "use_cache" in fields:
        if req.use_cache is None:
            raise ValueError("use_cache 不能为 null")
        updates["use_cache"] = _use_cache_values(req.use_cache)
    return updates


def build_feed_ops_capability() -> Capability[AgentDeps]:
    """按需加载的 RSS/Atom 订阅源与条目历史管理能力."""

    cap: Capability[AgentDeps] = Capability(
        id="feed-ops",
        description=(
            "Use for managing RSS/Atom feeds and their item history: create, update, poll, "
            "delete feeds, browse items, and batch ignore/unignore/delete/scrape items."
        ),
        instructions=(
            "Feed polling discovers items and may enqueue low-priority SCRAPE tasks according to "
            "the feed's auto_enqueue setting; it does not run scraping inline. "
            "Feed item scrape uses the feed's current content_type and cache settings. "
            "Deleting a feed or feed items requires user approval because it removes history."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def list_feeds(ctx: RunContext[AgentDeps]) -> dict[str, object]:
        """List all RSS/Atom feeds."""
        trace_tool(ctx, "tool_call", {"tool": "list_feeds"})
        feeds = await ctx.deps.repo.list_feeds()
        out = FeedListResult(items=[_feed_info(feed) for feed in feeds], total=len(feeds))
        result = out.model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "list_feeds", "result": result})
        return result

    @cap.tool
    async def get_feed(ctx: RunContext[AgentDeps], feed_id: int) -> dict[str, object]:
        """Get one feed by id."""
        trace_tool(ctx, "tool_call", {"tool": "get_feed", "feed_id": feed_id})
        feed = await ctx.deps.repo.get_feed(feed_id)
        if feed is None:
            return {"error": f"feed {feed_id} 不存在"}
        result = _feed_info(feed).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "get_feed", "result": result})
        return result

    @cap.tool
    async def create_feed(ctx: RunContext[AgentDeps], request: AgentFeedCreate) -> dict[str, object]:
        """Create a feed and poll it once when the FeedService bridge is available."""
        trace_tool(ctx, "tool_call", {"tool": "create_feed", "request": request.model_dump(mode="json")})
        try:
            name, url, group, number_pattern, use_cache = _feed_create_values(request)
            if await ctx.deps.repo.get_feed_by_url(url) is not None:
                return {"error": "该订阅源 URL 已存在"}
            feed = await ctx.deps.repo.create_feed(
                name=name,
                url=url,
                group=group,
                enabled=request.enabled,
                auto_enqueue=request.auto_enqueue,
                interval_seconds=request.interval_seconds,
                number_pattern=number_pattern,
                content_type=request.content_type,
                use_cache=use_cache,
            )
        except (IntegrityError, ValueError) as exc:
            return {"error": str(exc)}

        assert feed.id is not None
        poll = ctx.deps.bridge.poll_feed
        if poll is not None:
            try:
                await poll(feed.id)
                refreshed = await ctx.deps.repo.get_feed(feed.id)
                if refreshed is not None:
                    feed = refreshed
            except Exception as exc:
                result = _feed_info(feed).model_dump(mode="json")
                result["poll_error"] = str(exc)
                trace_tool(ctx, "tool_result", {"tool": "create_feed", "result": result})
                return result

        result = _feed_info(feed).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "create_feed", "result": result})
        return result

    @cap.tool
    async def update_feed(ctx: RunContext[AgentDeps], feed_id: int, patch: AgentFeedUpdate) -> dict[str, object]:
        """Patch user-editable feed settings."""
        trace_tool(
            ctx, "tool_call", {"tool": "update_feed", "feed_id": feed_id, "patch": patch.model_dump(mode="json")}
        )
        feed = await ctx.deps.repo.get_feed(feed_id)
        if feed is None:
            return {"error": f"feed {feed_id} 不存在"}
        try:
            updates = _feed_update_values(patch)
            url = updates.get("url")
            if isinstance(url, str):
                existing = await ctx.deps.repo.get_feed_by_url(url)
                if existing is not None and existing.id != feed_id:
                    return {"error": "该订阅源 URL 已存在"}
            updated = await ctx.deps.repo.update_feed(feed_id, **cast(FeedUpdates, updates))
        except (IntegrityError, ValueError) as exc:
            return {"error": str(exc)}
        if updated is None:
            return {"error": f"feed {feed_id} 不存在"}
        result = _feed_info(updated).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "update_feed", "result": result})
        return result

    @cap.tool
    async def poll_feed(ctx: RunContext[AgentDeps], feed_id: int) -> dict[str, object]:
        """Poll one feed now; discovered items may enqueue SCRAPE tasks."""
        trace_tool(ctx, "tool_call", {"tool": "poll_feed", "feed_id": feed_id})
        feed = await ctx.deps.repo.get_feed(feed_id)
        if feed is None:
            return {"error": f"feed {feed_id} 不存在"}
        poll = ctx.deps.bridge.poll_feed
        if poll is None:
            return {"error": "订阅源服务未启动"}
        try:
            await poll(feed_id)
        except Exception as exc:
            return {"error": f"拉取失败: {exc}"}
        refreshed = await ctx.deps.repo.get_feed(feed_id)
        if refreshed is None:
            return {"error": f"feed {feed_id} 不存在"}
        result = _feed_info(refreshed).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "poll_feed", "result": result})
        return result

    @cap.tool
    async def delete_feed(ctx: RunContext[AgentDeps], feed_id: int) -> dict[str, object]:
        """Delete a feed and its item history. Requires user approval."""
        detail = f"删除订阅源 id={feed_id} 及其条目历史"
        trace_tool(ctx, "tool_call", {"tool": "delete_feed", "feed_id": feed_id})
        require_approval(ctx, sql=detail, tool="delete_feed", extra={"feed_id": feed_id})
        deleted = await ctx.deps.repo.delete_feed(feed_id)
        result: dict[str, object] = {"tool": "delete_feed", "feed_id": feed_id, "deleted": deleted}
        if not deleted:
            result["error"] = f"feed {feed_id} 不存在"
        trace_tool(ctx, "tool_result", {"tool": "delete_feed", "result": result})
        return result

    @cap.tool
    async def list_feed_items(
        ctx: RunContext[AgentDeps],
        feed_id: int | None = None,
        search: str | None = None,
        state: FeedItemState = FeedItemState.ACTIVE,
        group: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, object]:
        """List feed history, optionally scoped by feed id or feed group."""
        if offset < 0:
            return {"error": "offset 不能小于 0"}
        if limit < 1 or limit > 200:
            return {"error": "limit 必须在 1 到 200 之间"}
        if feed_id is not None and await ctx.deps.repo.get_feed(feed_id) is None:
            return {"error": f"feed {feed_id} 不存在"}
        if feed_id is None and group is not None:
            try:
                group = normalize_feed_group(group)
            except ValueError as exc:
                return {"error": str(exc)}
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "list_feed_items",
                "feed_id": feed_id,
                "search": search,
                "state": state,
                "group": group,
                "offset": offset,
                "limit": limit,
            },
        )
        try:
            rows, total = await ctx.deps.repo.list_feed_items(
                feed_id,
                offset=offset,
                limit=limit,
                search=search.strip() if search is not None else None,
                state=state,
                group=group if feed_id is None else None,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        out = FeedItemListResult(
            items=[_feed_item_info(item, metadata_id) for item, metadata_id in rows],
            total=total,
            offset=offset,
            limit=limit,
        )
        result = out.model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "list_feed_items", "result": result})
        return result

    @cap.tool
    async def batch_feed_items(
        ctx: RunContext[AgentDeps], feed_id: int, request: AgentFeedItemBatch
    ) -> dict[str, object]:
        """Batch ignore, unignore, delete, or scrape feed items."""
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "batch_feed_items",
                "feed_id": feed_id,
                "request": request.model_dump(mode="json"),
            },
        )
        feed = await ctx.deps.repo.get_feed(feed_id)
        if feed is None:
            return {"error": f"feed {feed_id} 不存在"}

        if request.action is FeedItemBatchAction.DELETE:
            require_approval(
                ctx,
                sql=f"删除订阅源 {feed_id} 的条目 ids={request.ids}",
                tool="batch_feed_items",
                extra={"feed_id": feed_id, "action": request.action, "ids": list(request.ids)},
            )

        if request.action is FeedItemBatchAction.IGNORE:
            affected, missing = await ctx.deps.repo.ignore_feed_items(feed_id, request.ids)
            out = FeedBatchResult(action=request.action, affected=affected, missing=missing)
        elif request.action is FeedItemBatchAction.UNIGNORE:
            affected, missing = await ctx.deps.repo.unignore_feed_items(feed_id, request.ids)
            out = FeedBatchResult(action=request.action, affected=affected, missing=missing)
        elif request.action is FeedItemBatchAction.DELETE:
            affected, missing = await ctx.deps.repo.delete_feed_items(feed_id, request.ids)
            out = FeedBatchResult(action=request.action, affected=affected, missing=missing)
        else:
            items, missing = await ctx.deps.repo.list_feed_items_by_ids(feed_id, request.ids)
            seen_numbers: set[str] = set()
            payloads: list[ScrapePayload] = []
            skipped = 0
            for item in items:
                if not item.number:
                    skipped += 1
                    continue
                key = item.number.casefold()
                if key in seen_numbers:
                    continue
                seen_numbers.add(key)
                payloads.append(build_feed_scrape_payload(feed, item.number))
            tasks = await ctx.deps.repo.create_tasks(TaskType.SCRAPE, payloads, priority=0)
            out = FeedBatchResult(
                action=request.action,
                affected=len(items),
                missing=missing,
                skipped=skipped,
                submitted=len(tasks),
                task_ids=[task.id for task in tasks if task.id is not None],
            )

        result = out.model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "batch_feed_items", "result": result})
        return result

    return cap
