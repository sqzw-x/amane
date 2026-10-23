from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Response

from ...db.models import MediaFile, MediaFileStatus, MediaSortField, SortOrder
from ...desktop_file import can_open_desktop_file, open_desktop_file
from ...library.rules import is_video_media
from ...parsing import DEFINITION_VALUES, ContentType, Mosaic
from ..deps import RepoDep, RuntimeDep
from ..models import (
    MediaBatchDeleteRequest,
    MediaBatchDeleteResponse,
    MediaFileResponse,
    MediaFileUpdateRequest,
    MediaListResponse,
    to_media_file_response,
)
from ..support.path_validation import validate_file_path

if TYPE_CHECKING:
    from ...db.repo_types import MediaFileUpdates
    from ...db.repository import Repository

logger = structlog.get_logger()

router = APIRouter(prefix="/media", tags=["media"])


def _require_known_definition(definition: str | None) -> str | None:
    if definition is None:
        return None
    if definition not in DEFINITION_VALUES:
        raise HTTPException(status_code=422, detail=f"未知分辨率: {definition}")
    return definition


async def _phase_responses(repo: Repository, items: Sequence[MediaFile]) -> list[MediaFileResponse]:
    """文件行的有码 / 无码取自所属条目类型, 因此要批量补一次条目类型."""
    types = await repo.content_types_by_metadata_ids([m.metadata_id for m in items if m.metadata_id is not None])
    return [to_media_file_response(m, types.get(m.metadata_id) if m.metadata_id is not None else None) for m in items]


@router.get("")
async def list_media(
    repo: RepoDep,
    status: Annotated[MediaFileStatus | None, Query(description="Filter by media file status")] = None,
    search: Annotated[str | None, Query(description="Search by path or number")] = None,
    library_id: Annotated[int | None, Query(description="Filter by owning library")] = None,
    has_subtitle: Annotated[bool | None, Query(description="Filter by subtitle marker")] = None,
    mosaic: Annotated[Mosaic | None, Query(description="Filter by mosaic marker")] = None,
    uncensored: Annotated[
        bool | None, Query(description="Filter by uncensored (own marker or uncensored number content type)")
    ] = None,
    definition: Annotated[str | None, Query(description="Filter by definition (8K/4K/1080p/…)")] = None,
    content_type: Annotated[ContentType | None, Query(description="Filter by owning number content type")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Annotated[MediaSortField, Query(description="Sort field")] = MediaSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
) -> MediaListResponse:
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
    return MediaListResponse(items=await _phase_responses(repo, items), total=total)


@router.post("/batch/delete")
async def batch_delete_media(req: MediaBatchDeleteRequest, repo: RepoDep) -> MediaBatchDeleteResponse:
    """批量删除媒体文件记录; 不存在的 id 计入 missing 而不是报错."""
    deleted, missing = await repo.batch_delete_media_files(req.ids)
    logger.info("media files batch deleted", deleted=deleted, missing=missing)
    return MediaBatchDeleteResponse(deleted=deleted, missing=missing)


@router.get("/{media_id}")
async def get_media(media_id: int, repo: RepoDep) -> MediaFileResponse:
    media = await repo.get_media_file(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return (await _phase_responses(repo, [media]))[0]


@router.post("/{media_id}/open", status_code=204)
async def open_media_file(media_id: int, runtime: RuntimeDep) -> Response:
    """在运行 Amane 的桌面主机上使用默认播放器打开已登记的视频文件."""
    if not can_open_desktop_file():
        raise HTTPException(status_code=403, detail="当前运行环境不支持本机播放器")

    media = await runtime.repo.get_media_file(media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    path = await validate_file_path(media.path, runtime.safe_dirs)
    media_extensions = frozenset(runtime.config.hot.watcher.media_extensions)
    if not is_video_media(path, media_extensions):
        raise HTTPException(status_code=422, detail="关联文件不是已配置的视频类型")

    try:
        await open_desktop_file(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法打开本机播放器: {exc}") from exc
    return Response(status_code=204)


@router.patch("/{media_id}")
async def update_media(media_id: int, req: MediaFileUpdateRequest, repo: RepoDep) -> MediaFileResponse:
    updates = cast("MediaFileUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")
    media = await repo.update_media_file(media_id, **updates)
    if media is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    logger.info("media file updated", media_id=media_id, fields=list(updates.keys()))
    return (await _phase_responses(repo, [media]))[0]


@router.delete("/{media_id}", status_code=204)
async def delete_media(media_id: int, repo: RepoDep):
    deleted = await repo.delete_media_file(media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    logger.info("media file deleted", media_id=media_id)
    return Response(status_code=204)
