from typing import TYPE_CHECKING, Annotated, cast

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import TypeAdapter

from ...aggregate import compute_merge_updates
from ...db.models import MetadataSortField, SavedQueryEntity, SortOrder, TaskType
from ...db.repos.media import file_phase_of
from ...handlers import ScrapePayload
from ...media import manual_crop_poster
from ...parsing import DEFINITION_VALUES, ContentType, Mosaic, infer_content_type, summarize_file_phases
from ...parsing import FilePhaseSummary as ParsedFilePhase
from ...utils.dates import normalize_calendar_date
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    CommentResponse,
    CropPosterRequest,
    FilePhaseSummary,
    MediaFileResponse,
    MergeRequest,
    MetadataBatchDeleteResponse,
    MetadataBatchIdsRequest,
    MetadataBatchScrapeRequest,
    MetadataBatchScrapeResponse,
    MetadataBatchUserTagsRequest,
    MetadataBatchUserTagsResponse,
    MetadataDetailResponse,
    MetadataListResponse,
    MetadataResponse,
    PartialMetadata,
    UserTagResponse,
)
from .agent import resolve_saved_query_id_subquery

if TYPE_CHECKING:
    from ...db.repo_types import MetadataFields

logger = structlog.get_logger()

router = APIRouter(prefix="/metadata", tags=["metadata"])


def _file_phase_resp(summary: ParsedFilePhase) -> FilePhaseSummary:
    return FilePhaseSummary(
        has_subtitle=summary.has_subtitle,
        uncensored=summary.uncensored,
        mosaics=list(summary.mosaics),
        definition=summary.definition,
    )


def _require_known_definition(definition: str | None) -> str | None:
    if definition is None:
        return None
    if definition not in DEFINITION_VALUES:
        raise HTTPException(status_code=422, detail=f"未知清晰度: {definition}")
    return definition


@router.get("/schema")
async def get_metadata_schema() -> dict:
    return TypeAdapter(PartialMetadata).json_schema()


@router.get("")
async def list_metadata(
    repo: RepoDep,
    search: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    sort_by: Annotated[MetadataSortField, Query(description="Sort field")] = MetadataSortField.UPDATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
    actor_id: Annotated[list[int] | None, Query(description="Filter by actor facet id(s); AND")] = None,
    director_id: Annotated[list[int] | None, Query(description="Filter by director facet id(s); AND")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by scraped tag facet id(s); AND")] = None,
    studio_id: Annotated[list[int] | None, Query(description="Filter by studio facet id(s); OR")] = None,
    publisher_id: Annotated[list[int] | None, Query(description="Filter by publisher facet id(s); OR")] = None,
    series_id: Annotated[list[int] | None, Query(description="Filter by series facet id(s); OR")] = None,
    user_tag_id: Annotated[list[int] | None, Query(description="Filter by user tag id(s); AND")] = None,
    has_files: Annotated[bool | None, Query(description="Filter by presence of linked MediaFile(s)")] = None,
    has_subtitle: Annotated[bool | None, Query(description="Filter by linked file subtitle marker")] = None,
    mosaic: Annotated[Mosaic | None, Query(description="Filter by linked file mosaic marker")] = None,
    uncensored: Annotated[
        bool | None, Query(description="Filter by uncensored file (mosaic marker or uncensored content type)")
    ] = None,
    definition: Annotated[str | None, Query(description="Filter by linked file definition (8K/4K/1080p/…)")] = None,
    content_type: Annotated[ContentType | None, Query(description="Filter by linked file content type")] = None,
    saved_query_id: Annotated[
        int | None, Query(description="Saved query preset id; AND with other filters via SQL subquery")
    ] = None,
) -> MetadataListResponse:
    definition = _require_known_definition(definition)
    id_subquery_sql = None
    if saved_query_id is not None:
        id_subquery_sql = await resolve_saved_query_id_subquery(repo, saved_query_id, SavedQueryEntity.METADATA)
    items, total = await repo.list_metadata(
        keyword=search,
        offset=offset,
        limit=limit,
        sort_by=sort_by,
        order=order,
        actor_ids=actor_id,
        director_ids=director_id,
        tag_ids=tag_id,
        studio_ids=studio_id,
        publisher_ids=publisher_id,
        series_ids=series_id,
        user_tag_ids=user_tag_id,
        has_files=has_files,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
        id_subquery_sql=id_subquery_sql,
    )
    summaries = await repo.summarize_media_by_metadata_ids([m.id for m in items if m.id is not None])
    empty = FilePhaseSummary()
    return MetadataListResponse(
        items=[
            to_resp(MetadataResponse, m).model_copy(
                update={
                    "file_count": summaries[m.id].file_count if m.id in summaries else 0,
                    "file_phase": _file_phase_resp(summaries[m.id].phase) if m.id in summaries else empty,
                }
            )
            for m in items
        ],
        total=total,
    )


@router.post("/batch/delete")
async def batch_delete_metadata(req: MetadataBatchIdsRequest, repo: RepoDep) -> MetadataBatchDeleteResponse:
    """级联行为与单条删除一致."""
    deleted, missing = await repo.batch_delete_metadata(req.ids)
    logger.info("metadata batch deleted", deleted=deleted, missing=missing)
    return MetadataBatchDeleteResponse(deleted=deleted, missing=missing)


@router.post("/batch/scrape", status_code=202)
async def batch_scrape_metadata(req: MetadataBatchScrapeRequest, repo: RepoDep) -> MetadataBatchScrapeResponse:
    """以各自 number 重新刮削."""
    task_ids: list[int] = []
    missing = 0
    # 挂载文件仅用于 content_type 推断 (req.content_type 显式给定时跳过).
    files = await repo.list_media_files(metadata_ids=req.ids, limit=None)
    first_path_by_metadata: dict[int, str] = {}
    for f in files:
        if f.metadata_id is not None and f.metadata_id not in first_path_by_metadata:
            first_path_by_metadata[f.metadata_id] = f.path

    for metadata_id in req.ids:
        metadata = await repo.get_metadata(metadata_id)
        if metadata is None:
            missing += 1
            continue
        payload = ScrapePayload(
            number=metadata.number,
            content_type=req.content_type
            if req.content_type is not None
            else infer_content_type(metadata.number, first_path_by_metadata.get(metadata_id)),
            use_cache=req.use_cache,
        )
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload=payload)
        assert task.id is not None
        task_ids.append(task.id)
    logger.info("metadata batch scrape submitted", submitted=len(task_ids), missing=missing)
    return MetadataBatchScrapeResponse(submitted=len(task_ids), missing=missing, task_ids=task_ids)


@router.post("/batch/user-tags")
async def batch_metadata_user_tags(req: MetadataBatchUserTagsRequest, repo: RepoDep) -> MetadataBatchUserTagsResponse:
    if req.action == "attach":
        affected, missing = await repo.batch_attach_user_tag(req.ids, req.user_tag_id)
    else:
        affected, missing = await repo.batch_detach_user_tag(req.ids, req.user_tag_id)
    logger.info("metadata batch user tags", action=req.action, affected=affected, missing=missing)
    return MetadataBatchUserTagsResponse(affected=affected, missing=missing)


@router.get("/{metadata_id}")
async def get_metadata(metadata_id: int, repo: RepoDep) -> MetadataDetailResponse:
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    # 关联文件 / 用户 tag / 评论 / 分类 id
    files = await repo.get_media_by_metadata_id(metadata_id)
    user_tags = await repo.list_metadata_user_tags(metadata_id)
    comments = await repo.list_comments(metadata_id)
    (
        actor_ids,
        actor_genders,
        director_ids,
        tag_ids,
        studio_id,
        publisher_id,
        series_id,
    ) = await repo.resolve_metadata_facet_ids(metadata)
    return MetadataDetailResponse(
        metadata=to_resp(MetadataResponse, metadata).model_copy(
            update={
                "file_count": len(files),
                "file_phase": _file_phase_resp(summarize_file_phases(file_phase_of(f) for f in files)),
            }
        ),
        files=[to_resp(MediaFileResponse, f) for f in files],
        user_tags=[to_resp(UserTagResponse, t) for t in user_tags],
        comments=[to_resp(CommentResponse, c) for c in comments],
        actor_ids=actor_ids,
        actor_genders=actor_genders,
        director_ids=director_ids,
        tag_ids=tag_ids,
        studio_id=studio_id,
        publisher_id=publisher_id,
        series_id=series_id,
    )


@router.put("/{metadata_id}/user-tags/{user_tag_id}", status_code=204)
async def attach_user_tag(metadata_id: int, user_tag_id: int, repo: RepoDep) -> None:
    ok = await repo.attach_user_tag(metadata_id, user_tag_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Metadata or user tag not found")


@router.delete("/{metadata_id}/user-tags/{user_tag_id}", status_code=204)
async def detach_user_tag(metadata_id: int, user_tag_id: int, repo: RepoDep) -> None:
    ok = await repo.detach_user_tag(metadata_id, user_tag_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User tag attachment not found")


@router.patch("/{metadata_id}")
async def update_metadata(metadata_id: int, req: PartialMetadata, repo: RepoDep) -> MetadataResponse:
    updates = cast("MetadataFields", {k: v for k, v in req.model_dump().items() if v is not None})
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    if "release" in updates:
        raw_release = updates["release"]
        if isinstance(raw_release, str):
            normalized = normalize_calendar_date(raw_release)
            if normalized is None:
                raise HTTPException(status_code=422, detail="release must be YYYY-MM-DD")
            updates["release"] = normalized
        else:
            raise HTTPException(status_code=422, detail="release must be YYYY-MM-DD")
    metadata = await repo.update_metadata(metadata_id, **updates)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    logger.info("metadata updated", metadata_id=metadata_id, fields=list(updates.keys()))
    return to_resp(MetadataResponse, metadata)


@router.delete("/{metadata_id}", status_code=204)
async def delete_metadata(metadata_id: int, repo: RepoDep) -> None:
    deleted = await repo.delete_metadata(metadata_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Metadata not found")
    logger.info("metadata deleted", metadata_id=metadata_id)


@router.post("/{metadata_id}/crop-poster")
async def crop_poster_from_thumb(
    metadata_id: int,
    req: CropPosterRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> MetadataResponse:
    """从封面 (thumb) 按像素框裁切海报, 写入派生 Resource 并更新 poster_urls."""
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    if not metadata.thumb_urls:
        raise HTTPException(status_code=400, detail="无封面图可裁切")

    # 按像素框裁切封面, 写入派生 Resource
    thumb_url = metadata.thumb_urls[0]
    box = (req.left, req.top, req.right, req.bottom)
    try:
        poster_url = await manual_crop_poster(
            thumb_url,
            box,
            runtime.resource_store,
            runtime.web_client,
            runtime.config.hot,
            runtime.config.cold.data_dir,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 写回 poster_urls
    updated = await repo.update_metadata(metadata_id, poster_urls=[poster_url])
    assert updated is not None
    logger.info(
        "poster cropped",
        metadata_id=metadata_id,
        box=box,
        poster_url=poster_url,
    )
    return to_resp(MetadataResponse, updated)


@router.post("/{metadata_id}/merge")
async def merge_metadata(metadata_id: int, req: MergeRequest, repo: RepoDep) -> MetadataResponse:
    if not req.selections:
        raise HTTPException(status_code=400, detail="no selections provided")

    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Metadata not found")

    # 按 selections 从 raw 合并字段
    try:
        updates = compute_merge_updates(metadata.raw, metadata.field_sources, req.selections)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not updates:
        raise HTTPException(status_code=400, detail="no valid selections")

    # 写回元数据
    updated = await repo.update_metadata(metadata_id, **cast("MetadataFields", updates))
    assert updated is not None
    logger.info("metadata merged", metadata_id=metadata_id, selections=req.selections)
    return to_resp(MetadataResponse, updated)
