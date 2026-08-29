from typing import TYPE_CHECKING, Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from ...db import FeedItem, FeedItemState, TaskType
from ...handlers.models import CacheKind, ScrapePayload, build_feed_scrape_payload
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models.feeds import (
    FeedCreateRequest,
    FeedItemBatchAction,
    FeedItemBatchRequest,
    FeedItemBatchResponse,
    FeedItemListResponse,
    FeedItemResponse,
    FeedListResponse,
    FeedResponse,
    FeedUpdateRequest,
    _validate_http_url,
    _validate_number_pattern,
    normalize_feed_group,
)

if TYPE_CHECKING:
    from ...db.repo_types import FeedUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/feeds", tags=["feeds"])


_CACHE_KIND_ORDER = (CacheKind.metadata, CacheKind.trans)


def _use_cache_values(use_cache: set[CacheKind]) -> list[str]:
    return [kind for kind in _CACHE_KIND_ORDER if kind in use_cache]


def _item_resp(item: FeedItem, metadata_id: int | None) -> FeedItemResponse:
    assert item.id is not None
    return FeedItemResponse(
        id=item.id,
        feed_id=item.feed_id,
        item_key=item.item_key,
        title=item.title,
        link=item.link,
        description=item.description,
        number=item.number,
        published_at=item.published_at,
        created_at=item.created_at,
        ignored_at=item.ignored_at,
        metadata_id=metadata_id,
    )


@router.get("")
async def list_feeds(repo: RepoDep) -> FeedListResponse:
    items = await repo.list_feeds()
    return FeedListResponse(items=[to_resp(FeedResponse, feed) for feed in items], total=len(items))


@router.post("", status_code=201)
async def create_feed(req: FeedCreateRequest, repo: RepoDep, runtime: RuntimeDep) -> FeedResponse:
    name = req.name.strip()
    if await repo.get_feed_by_url(req.url) is not None:
        raise HTTPException(status_code=409, detail="该订阅源 URL 已存在")
    try:
        feed = await repo.create_feed(
            name=name,
            url=req.url,
            group=req.group,
            enabled=req.enabled,
            auto_enqueue=req.auto_enqueue,
            interval_seconds=req.interval_seconds,
            number_pattern=req.number_pattern,
            content_type=req.content_type,
            use_cache=_use_cache_values(req.use_cache),
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该订阅源 URL 已存在") from exc
    assert feed.id is not None
    logger.info("feed created", feed_id=feed.id, url=feed.url)
    if runtime.feed_service is not None:
        await runtime.feed_service.poll_one(feed.id)
        refreshed = await repo.get_feed(feed.id)
        if refreshed is not None:
            feed = refreshed
    return to_resp(FeedResponse, feed)


@router.get("/items")
async def list_all_feed_items(
    repo: RepoDep,
    search: Annotated[str | None, Query()] = None,
    state: Annotated[FeedItemState, Query()] = FeedItemState.ACTIVE,
    feed_id: Annotated[int | None, Query()] = None,
    group: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedItemListResponse:
    """跨源条目列表. feed_id 优先于 group; group 空串只含未分组源, 非空为前缀匹配."""
    if feed_id is not None:
        feed = await repo.get_feed(feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="订阅源不存在")
        normalized_group = None
    elif group is not None:
        try:
            normalized_group = normalize_feed_group(group)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        normalized_group = None
    items, total = await repo.list_feed_items(
        feed_id,
        offset=offset,
        limit=limit,
        search=search.strip() if search is not None else None,
        state=state,
        group=normalized_group if feed_id is None else None,
    )
    return FeedItemListResponse(items=[_item_resp(item, metadata_id) for item, metadata_id in items], total=total)


@router.get("/{feed_id}")
async def get_feed(feed_id: int, repo: RepoDep) -> FeedResponse:
    feed = await repo.get_feed(feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    return to_resp(FeedResponse, feed)


@router.patch("/{feed_id}")
async def update_feed(feed_id: int, req: FeedUpdateRequest, repo: RepoDep) -> FeedResponse:
    feed = await repo.get_feed(feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    updates = cast("FeedUpdates", req.model_dump(exclude_unset=True))
    if "url" in updates:
        updates["url"] = _validate_http_url(str(updates["url"]))
        existing = await repo.get_feed_by_url(updates["url"])
        if existing is not None and existing.id != feed_id:
            raise HTTPException(status_code=409, detail="该订阅源 URL 已存在")
    if "number_pattern" in updates:
        updates["number_pattern"] = _validate_number_pattern(
            str(updates["number_pattern"]) if updates["number_pattern"] is not None else None
        )
    if "name" in updates:
        updates["name"] = str(updates["name"]).strip()
    if "group" in updates:
        try:
            updates["group"] = normalize_feed_group(str(updates["group"]) if updates["group"] is not None else "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "interval_seconds" in updates:
        interval = int(updates["interval_seconds"])
        if interval < 60 or interval > 86400:
            raise HTTPException(status_code=422, detail="interval_seconds 必须在 60 到 86400 之间")
        updates["interval_seconds"] = interval
    if "use_cache" in updates:
        raw = updates["use_cache"]
        kinds: set[CacheKind] = set()
        for item in raw:
            try:
                kinds.add(CacheKind(item))
            except TypeError, ValueError:
                continue
        updates["use_cache"] = _use_cache_values(kinds)
    try:
        updated = await repo.update_feed(feed_id, **updates)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该订阅源 URL 已存在") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    logger.info("feed updated", feed_id=feed_id, fields=list(updates.keys()))
    return to_resp(FeedResponse, updated)


@router.delete("/{feed_id}", status_code=204)
async def delete_feed(feed_id: int, repo: RepoDep) -> Response:
    deleted = await repo.delete_feed(feed_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    logger.info("feed deleted", feed_id=feed_id)
    return Response(status_code=204)


@router.post("/{feed_id}/poll", status_code=202)
async def poll_feed(feed_id: int, repo: RepoDep, runtime: RuntimeDep) -> FeedResponse:
    feed = await repo.get_feed(feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    if runtime.feed_service is None:
        raise HTTPException(status_code=503, detail="订阅源服务未启动")
    await runtime.feed_service.poll_one(feed_id)
    refreshed = await repo.get_feed(feed_id)
    assert refreshed is not None
    logger.info("feed polled manually", feed_id=feed_id)
    return to_resp(FeedResponse, refreshed)


@router.get("/{feed_id}/items")
async def list_feed_items(
    feed_id: int,
    repo: RepoDep,
    search: Annotated[str | None, Query()] = None,
    state: Annotated[FeedItemState, Query()] = FeedItemState.ACTIVE,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedItemListResponse:
    feed = await repo.get_feed(feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")
    items, total = await repo.list_feed_items(
        feed_id,
        offset=offset,
        limit=limit,
        search=search.strip() if search is not None else None,
        state=state,
    )
    return FeedItemListResponse(items=[_item_resp(item, metadata_id) for item, metadata_id in items], total=total)


@router.post("/{feed_id}/items/batch", response_model_exclude_unset=True)
async def batch_feed_items(feed_id: int, req: FeedItemBatchRequest, repo: RepoDep) -> FeedItemBatchResponse:
    feed = await repo.get_feed(feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="订阅源不存在")

    response: FeedItemBatchResponse
    match req.action:
        case FeedItemBatchAction.IGNORE:
            affected, missing = await repo.ignore_feed_items(feed_id, req.ids)
            response = FeedItemBatchResponse(affected=affected, missing=missing)
        case FeedItemBatchAction.UNIGNORE:
            affected, missing = await repo.unignore_feed_items(feed_id, req.ids)
            response = FeedItemBatchResponse(affected=affected, missing=missing)
        case FeedItemBatchAction.DELETE:
            affected, missing = await repo.delete_feed_items(feed_id, req.ids)
            response = FeedItemBatchResponse(affected=affected, missing=missing)
        case FeedItemBatchAction.SCRAPE:
            items, missing = await repo.list_feed_items_by_ids(feed_id, req.ids)
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

            tasks = await repo.create_tasks(TaskType.SCRAPE, payloads, priority=0)
            task_ids = [task.id for task in tasks if task.id is not None]
            response = FeedItemBatchResponse(
                affected=len(items),
                missing=missing,
                skipped=skipped,
                submitted=len(task_ids),
                task_ids=task_ids,
            )
    logger.info(
        "feed items batch action",
        feed_id=feed_id,
        action=req.action,
        affected=response.affected,
        missing=response.missing,
        skipped=response.skipped,
        submitted=response.submitted,
    )
    return response
