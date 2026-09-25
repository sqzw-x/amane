"""身份治理 (rename/merge/delete/rules) 仍由 /facets/actor 处理."""

from typing import Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query

from ...db.models import Actor, FacetKind, SavedQueryEntity, TaskType
from ...db.repo_types import ActorBrowseItem, ActorBrowseParams, ActorPersonFields
from ...handlers import ActorScrapePayload
from ...utils.dates import normalize_calendar_date
from ...utils.model import to_resp
from ..deps import RepoDep
from ..models import (
    ActorListResponse,
    ActorResponse,
    ActorScrapeRequest,
    ActorUpdateRequest,
    ActorUserTagsRequest,
    TaskResponse,
    UserTagLinksResponse,
    UserTagResponse,
)
from .agent import resolve_saved_query_id_subquery

logger = structlog.get_logger()

router = APIRouter(prefix="/actors", tags=["actors"])


def _from_browse(item: ActorBrowseItem) -> ActorResponse:
    """列表只填卡片/表格字段; 简介/别名/标签/源字典见详情."""
    return ActorResponse(
        id=item.id,
        name=item.name,
        count=item.count,
        gender=item.gender,
        birthday=item.birthday,
        birthplace=item.birthplace,
        height=item.height,
        bust=item.bust,
        waist=item.waist,
        hip=item.hip,
        cup=item.cup,
        image_urls=list(item.image_urls),
        updated_at=item.updated_at,
    )


def _from_actor(
    actor: Actor,
    *,
    count: int,
    aliases: list[str] | None = None,
    user_tags: list[UserTagResponse] | None = None,
    include_raw: bool = False,
) -> ActorResponse:
    assert actor.id is not None
    return ActorResponse(
        id=actor.id,
        name=actor.name,
        count=count,
        aliases=list(aliases or []),
        user_tags=list(user_tags or []),
        gender=actor.gender,
        birthday=actor.birthday,
        birthplace=actor.birthplace,
        height=actor.height,
        bust=actor.bust,
        waist=actor.waist,
        hip=actor.hip,
        cup=actor.cup,
        overview=actor.overview,
        tagline=actor.tagline,
        image_urls=list(actor.image_urls or []),
        provider_ids=dict(actor.provider_ids or {}),
        source_urls=dict(actor.source_urls or {}),
        field_sources=dict(actor.field_sources or {}),
        raw=dict(actor.raw or {}) if include_raw else {},
        updated_at=actor.updated_at,
    )


async def _actor_user_tags(repo: RepoDep, actor_id: int) -> list[UserTagResponse]:
    return [to_resp(UserTagResponse, tag) for tag in await repo.list_actor_user_tags(actor_id)]


@router.get("")
async def list_actors(repo: RepoDep, params: Annotated[ActorBrowseParams, Query()]) -> ActorListResponse:
    id_subquery_sql = None
    if params.saved_query_id is not None:
        id_subquery_sql = await resolve_saved_query_id_subquery(repo, params.saved_query_id, SavedQueryEntity.ACTOR)
    items, total = await repo.browse_actors(params, id_subquery_sql=id_subquery_sql)
    return ActorListResponse(items=[_from_browse(i) for i in items], total=total)


@router.post("/batch/user-tags")
async def batch_actor_user_tags(req: ActorUserTagsRequest, repo: RepoDep) -> UserTagLinksResponse:
    """把一组用户标签应用到一组演员; 未知标签 id 返回 404, 不存在的演员 id 计入 missing."""
    try:
        result = await repo.apply_actor_user_tags(req.ids, req.user_tag_ids, action=req.action)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    logger.info(
        "actor user tags applied",
        action=req.action,
        changed=result.changed,
        unchanged=result.unchanged,
        missing=result.missing,
    )
    return UserTagLinksResponse(changed=result.changed, unchanged=result.unchanged, missing=result.missing)


@router.get("/{actor_id}")
async def get_actor(actor_id: int, repo: RepoDep) -> ActorResponse:
    item = await repo.get_facet(FacetKind.ACTOR, actor_id)
    actor = await repo.get_actor(actor_id)
    if item is None or actor is None:
        raise HTTPException(status_code=404, detail="Actor not found")
    assert actor.id is not None
    aliases = await repo.get_actor_aliases(actor.id)
    user_tags = await _actor_user_tags(repo, actor.id)
    return _from_actor(actor, count=item.count, aliases=aliases, user_tags=user_tags, include_raw=True)


@router.patch("/{actor_id}")
async def update_actor(actor_id: int, req: ActorUpdateRequest, repo: RepoDep) -> ActorResponse:
    """更新演员规范人物字段 (不含 name/raw/field_sources)."""
    updates = cast("ActorPersonFields", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    if "birthday" in updates:
        raw_bday = updates["birthday"]
        if raw_bday is None or (isinstance(raw_bday, str) and not raw_bday.strip()):
            updates["birthday"] = None
        elif isinstance(raw_bday, str):
            normalized = normalize_calendar_date(raw_bday)
            if normalized is None:
                raise HTTPException(status_code=422, detail="birthday must be YYYY-MM-DD")
            updates["birthday"] = normalized
        else:
            raise HTTPException(status_code=422, detail="birthday must be YYYY-MM-DD")
    actor = await repo.update_actor(actor_id, **updates)
    if actor is None:
        raise HTTPException(status_code=404, detail="Actor not found")
    item = await repo.get_facet(FacetKind.ACTOR, actor_id)
    count = item.count if item is not None else 0
    assert actor.id is not None
    aliases = await repo.get_actor_aliases(actor.id)
    user_tags = await _actor_user_tags(repo, actor.id)
    return _from_actor(actor, count=count, aliases=aliases, user_tags=user_tags, include_raw=True)


@router.post("/{actor_id}/scrape", status_code=202)
async def scrape_actor(actor_id: int, repo: RepoDep, req: ActorScrapeRequest | None = None) -> TaskResponse:
    actor = await repo.get_actor(actor_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="Actor not found")
    body = req or ActorScrapeRequest()
    task = await repo.create_task(
        task_type=TaskType.ACTOR_SCRAPE, payload=ActorScrapePayload(actor_id=actor_id, use_cache=body.use_cache)
    )
    logger.info("actor scrape task created", actor_id=actor_id, task_id=task.id)
    return to_resp(TaskResponse, task)
