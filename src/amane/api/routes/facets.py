from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from ...db.models import SCRAPE_FACET_KINDS, FacetKind, FacetSortField, SortOrder
from ...db.repo_types import FacetItem
from ..deps import RepoDep
from ..models import (
    FacetCreateRequest,
    FacetListResponse,
    FacetMergeRequest,
    FacetRenameRequest,
    FacetResponse,
    FacetRuleListResponse,
    FacetRuleResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/facets", tags=["facets"])


def _facet_response(item: FacetItem) -> FacetResponse:
    return FacetResponse(id=item.id, name=item.name, count=item.count)


@router.post("/user_tag", status_code=201)
async def create_user_tag(req: FacetCreateRequest, repo: RepoDep) -> FacetResponse:
    """创建用户标签."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    try:
        tag = await repo.create_user_tag(name)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="用户标签名称已存在") from e
    assert tag.id is not None
    return FacetResponse(id=tag.id, name=tag.name, count=0)


@router.get("/{kind}")
async def list_facets(
    kind: FacetKind,
    repo: RepoDep,
    search: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    sort_by: Annotated[FacetSortField, Query(description="Sort field")] = FacetSortField.NAME,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.ASC,
) -> FacetListResponse:
    """分页列出分类目录条目 (演员/导演/标签/厂商/发行商/系列/用户 tag)."""
    items, total = await repo.list_facets(kind, search=search, offset=offset, limit=limit, sort_by=sort_by, order=order)
    return FacetListResponse(items=[_facet_response(i) for i in items], total=total)


@router.get("/{kind}/rules")
async def list_facet_rules(kind: FacetKind, repo: RepoDep) -> FacetRuleListResponse:
    """列出爬取侧分类的用户规则 (别名 / 黑名单)."""
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
        raise HTTPException(status_code=404, detail="Rule not found")
    logger.info("facet rule deleted", kind=kind, rule_id=rule_id)
    return Response(status_code=204)


@router.get("/{kind}/{facet_id}")
async def get_facet(kind: FacetKind, facet_id: int, repo: RepoDep) -> FacetResponse:
    item = await repo.get_facet(kind, facet_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Facet not found")
    return _facet_response(item)


@router.post("/{kind}/merge")
async def merge_facets(kind: FacetKind, req: FacetMergeRequest, repo: RepoDep) -> FacetResponse:
    """将 source_ids 合并入 target_id: 关联迁移到 target, source 实体被删除."""
    try:
        item = await repo.merge_facets(kind, req.target_id, req.source_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="Facet not found")
    logger.info("facets merged", kind=kind, target_id=req.target_id, source_ids=req.source_ids)
    return _facet_response(item)


@router.patch("/{kind}/{facet_id}")
async def rename_facet(kind: FacetKind, facet_id: int, req: FacetRenameRequest, repo: RepoDep) -> FacetResponse:
    """重命名分类实体. 新名称与另一实体冲突时返回 409 (建议改用合并)."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    try:
        item = await repo.rename_facet(kind, facet_id, name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="Facet not found")
    logger.info("facet renamed", kind=kind, facet_id=facet_id, name=name)
    return _facet_response(item)


@router.delete("/{kind}/{facet_id}", status_code=204)
async def delete_facet(kind: FacetKind, facet_id: int, repo: RepoDep) -> Response:
    """删除分类. 爬取侧写入黑名单并从 Metadata 真值剔除; user_tag 硬删."""
    try:
        ok = await repo.delete_facet(kind, facet_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="Facet not found")
    logger.info("facet deleted", kind=kind, facet_id=facet_id)
    return Response(status_code=204)
