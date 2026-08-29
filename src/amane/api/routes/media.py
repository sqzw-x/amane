from typing import TYPE_CHECKING, Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Response

from ...db.models import MediaFileStatus, MediaSortField, SortOrder
from ...parsing import DEFINITION_VALUES, ContentType, Mosaic
from ...utils.model import to_resp
from ..deps import RepoDep
from ..models import MediaFileResponse, MediaFileUpdateRequest, MediaListResponse

if TYPE_CHECKING:
    from ...db.repo_types import MediaFileUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/media", tags=["media"])


def _require_known_definition(definition: str | None) -> str | None:
    if definition is None:
        return None
    if definition not in DEFINITION_VALUES:
        raise HTTPException(status_code=422, detail=f"未知清晰度: {definition}")
    return definition


@router.get("")
async def list_media(
    repo: RepoDep,
    status: Annotated[MediaFileStatus | None, Query(description="Filter by media file status")] = None,
    search: Annotated[str | None, Query(description="Search by path or number")] = None,
    library_id: Annotated[int | None, Query(description="Filter by owning library")] = None,
    has_subtitle: Annotated[bool | None, Query(description="Filter by subtitle marker")] = None,
    mosaic: Annotated[Mosaic | None, Query(description="Filter by mosaic marker")] = None,
    uncensored: Annotated[
        bool | None, Query(description="Filter by uncensored (mosaic marker or uncensored content type)")
    ] = None,
    definition: Annotated[str | None, Query(description="Filter by definition (8K/4K/1080p/…)")] = None,
    content_type: Annotated[ContentType | None, Query(description="Filter by content type")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Annotated[MediaSortField, Query(description="Sort field")] = MediaSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
) -> MediaListResponse:
    """列出媒体文件, 支持可选过滤, 服务端分页与排序"""
    definition = _require_known_definition(definition)
    status_filter = [status] if status is not None else None
    items = await repo.list_media_files(
        status_filter,
        limit,
        offset,
        search,
        library_id,
        sort_by,
        order,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
    )
    total = await repo.count_media_files(
        status=status_filter,
        search=search,
        library_id=library_id,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
    )
    return MediaListResponse(items=[to_resp(MediaFileResponse, m) for m in items], total=total)


@router.get("/{media_id}")
async def get_media(media_id: int, repo: RepoDep) -> MediaFileResponse:
    """根据 ID 获取单个媒体文件"""
    media = await repo.get_media_file(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    return to_resp(MediaFileResponse, media)


@router.patch("/{media_id}")
async def update_media(media_id: int, req: MediaFileUpdateRequest, repo: RepoDep) -> MediaFileResponse:
    """更新媒体文件的状态或番号"""
    updates = cast("MediaFileUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    media = await repo.update_media_file(media_id, **updates)
    if media is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    logger.info("media file updated", media_id=media_id, fields=list(updates.keys()))
    return to_resp(MediaFileResponse, media)


@router.delete("/{media_id}", status_code=204)
async def delete_media(media_id: int, repo: RepoDep):
    """删除媒体文件记录"""
    deleted = await repo.delete_media_file(media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Media file not found")
    logger.info("media file deleted", media_id=media_id)
    return Response(status_code=204)
