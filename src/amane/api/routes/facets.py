from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query, Response

from ...db.models import FAVORITE_FACET_KINDS, SCRAPE_FACET_KINDS, FacetKind, FacetSortField, SortOrder
from ...db.repo_types import FacetItem
from ...utils.model import to_resp
from ..deps import RepoDep
from ..models import (
    FacetFavoriteBatchRequest,
    FacetFavoriteBatchResponse,
    FacetFavoriteRequest,
    FacetListResponse,
    FacetMergeRequest,
    FacetRenameRequest,
    FacetResponse,
    FacetRuleListResponse,
    FacetRuleResponse,
    UserTagResponse,
    UserTagsCreateRequest,
    UserTagsCreateResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/facets", tags=["facets"])


def _facet_response(item: FacetItem) -> FacetResponse:
    return FacetResponse(id=item.id, name=item.name, count=item.count, is_favorite=item.is_favorite)


@router.post("/user_tag")
async def create_user_tags(req: UserTagsCreateRequest, repo: RepoDep) -> UserTagsCreateResponse:
    """按名称取回或新建用户标签; 已存在的名称直接复用, 响应与入参同序."""
    tags, created = await repo.ensure_user_tags(req.names)
    logger.info("user tags ensured", requested=len(req.names), created=created)
    return UserTagsCreateResponse(items=[to_resp(UserTagResponse, tag) for tag in tags], created=created)


@router.get("/{kind}")
async def list_facets(
    kind: FacetKind,
    repo: RepoDep,
    search: Annotated[str | None, Query()] = None,
    favorite: Annotated[bool | None, Query(description="Filter by favorite marker")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    sort_by: Annotated[FacetSortField, Query(description="Sort field")] = FacetSortField.NAME,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.ASC,
) -> FacetListResponse:
    # 传了参数就要求该分类支持收藏, 与取值无关; 静默忽略会让调用方以为筛选生效.
    if favorite is not None and kind not in FAVORITE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持收藏")
    items, total = await repo.list_facets(
        kind, search=search, favorite=favorite, offset=offset, limit=limit, sort_by=sort_by, order=order
    )
    return FacetListResponse(items=[_facet_response(i) for i in items], total=total)


@router.get("/{kind}/rules")
async def list_facet_rules(kind: FacetKind, repo: RepoDep) -> FacetRuleListResponse:
    if kind not in SCRAPE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持规则")
    try:
        rules = await repo.list_facet_rules(kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return FacetRuleListResponse(
        items=[
            FacetRuleResponse(
                id=r.id or 0,
                kind=str(r.kind),
                source_name=r.source_name,
                action=str(r.action),
                target_name=r.target_name,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rules
        ]
    )


@router.delete("/{kind}/rules/{rule_id}", status_code=204)
async def delete_facet_rule(kind: FacetKind, rule_id: int, repo: RepoDep) -> Response:
    """删除单条规则; 不回填历史 Metadata."""
    if kind not in SCRAPE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持规则")
    try:
        ok = await repo.delete_facet_rule(kind, rule_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="规则不存在")
    logger.info("facet rule deleted", kind=kind, rule_id=rule_id)
    return Response(status_code=204)


@router.put("/{kind}/batch/favorite")
async def set_facets_favorite(
    kind: FacetKind, req: FacetFavoriteBatchRequest, repo: RepoDep
) -> FacetFavoriteBatchResponse:
    """对一批分类整体赋值同一个收藏位, 单个事务; 分类不支持收藏返回 400.

    语义与单条端点一致, 但幂等判定作用于整批: 已处于目标取值的条目保持不动, 不存在的 id
    计入 ``missing`` 而不报 404 — 批量请求里某个 id 失效不该让整批无结果.
    """
    if kind not in FAVORITE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持收藏")
    result = await repo.set_facets_favorite(kind, req.facet_ids, req.is_favorite)
    logger.info(
        "facet favorites set",
        kind=kind,
        is_favorite=req.is_favorite,
        changed=result.changed,
        unchanged=result.unchanged,
        missing=result.missing,
    )
    return FacetFavoriteBatchResponse(changed=result.changed, unchanged=result.unchanged, missing=result.missing)


@router.get("/{kind}/{facet_id}")
async def get_facet(kind: FacetKind, facet_id: int, repo: RepoDep) -> FacetResponse:
    item = await repo.get_facet(kind, facet_id)
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    return _facet_response(item)


@router.put("/{kind}/{facet_id}/favorite")
async def set_facet_favorite(kind: FacetKind, facet_id: int, req: FacetFavoriteRequest, repo: RepoDep) -> FacetResponse:
    """整体赋值收藏位; 重复提交同一取值不改变结果. 分类不支持收藏返回 400."""
    if kind not in FAVORITE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持收藏")
    item = await repo.set_facet_favorite(kind, facet_id, req.is_favorite)
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facet favorite set", kind=kind, facet_id=facet_id, is_favorite=req.is_favorite)
    return _facet_response(item)


@router.post("/{kind}/merge")
async def merge_facets(kind: FacetKind, req: FacetMergeRequest, repo: RepoDep) -> FacetResponse:
    """将 source_ids 合并入 target_id: 关联迁移到 target, source 实体被删除."""
    try:
        item = await repo.merge_facets(kind, req.target_id, req.source_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facets merged", kind=kind, target_id=req.target_id, source_ids=req.source_ids)
    return _facet_response(item)


@router.patch("/{kind}/{facet_id}")
async def rename_facet(kind: FacetKind, facet_id: int, req: FacetRenameRequest, repo: RepoDep) -> FacetResponse:
    """新名称与另一实体冲突时返回 409 (应使用合并)."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    try:
        item = await repo.rename_facet(kind, facet_id, name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facet renamed", kind=kind, facet_id=facet_id, name=name)
    return _facet_response(item)


@router.delete("/{kind}/{facet_id}", status_code=204)
async def delete_facet(kind: FacetKind, facet_id: int, repo: RepoDep) -> Response:
    """删除分类. 爬取侧写入剔除规则并从 Metadata 真值移除; user_tag 硬删."""
    try:
        ok = await repo.delete_facet(kind, facet_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facet deleted", kind=kind, facet_id=facet_id)
    return Response(status_code=204)
