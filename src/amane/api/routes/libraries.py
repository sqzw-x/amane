from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from fastapi import APIRouter, HTTPException, Response

from ...db.models import TaskType
from ...handlers import RefreshPayload, ScanMode
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    LibraryCreateRequest,
    LibraryListResponse,
    LibraryResponse,
    LibraryUpdateRequest,
    PathTemplateSchemaResponse,
    last_organize_from_task,
    path_template_schema,
)
from ..support.path_validation import validate_directory_path

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ...db import Library, Task
    from ...db.repo_types import LibraryUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/libraries", tags=["libraries"])


def _library_response(lib: Library, latest: Mapping[int, Task] | None = None) -> LibraryResponse:
    resp = to_resp(LibraryResponse, lib)
    task = None if latest is None or lib.id is None else latest.get(lib.id)
    resp.last_organize = last_organize_from_task(task)
    return resp


async def _library_response_with_history(repo: RepoDep, lib: Library) -> LibraryResponse:
    assert lib.id is not None
    latest = await repo.latest_organize_tasks([lib.id])
    return _library_response(lib, latest)


@router.get("/path-template-schema")
async def get_path_template_schema() -> PathTemplateSchemaResponse:
    """与 resolve_paths 同源."""
    return path_template_schema()


@router.get("")
async def list_libraries(repo: RepoDep) -> LibraryListResponse:
    items = await repo.list_libraries()
    latest = await repo.latest_organize_tasks([lib.id for lib in items if lib.id is not None])
    return LibraryListResponse(items=[_library_response(lib, latest) for lib in items])


@router.post("", status_code=201)
async def create_library(req: LibraryCreateRequest, repo: RepoDep, runtime: RuntimeDep) -> LibraryResponse:
    # 校验路径: 须存在、是目录、位于 safe_dirs 内
    await validate_directory_path(req.path, runtime.safe_dirs)

    name = req.name or Path(req.path).name

    try:
        lib = await repo.create_library(
            name=name,
            path=req.path,
            automation=req.automation,
            auto_organize=req.auto_organize,
            ingest=req.ingest,
            cloud_path=req.cloud_path,
            recursive=req.recursive,
            patterns=req.patterns,
            move_mode=req.move_mode,
            video_template=req.video_template,
            link_template=req.link_template,
            link_mode=req.link_mode,
            strm_content_template=req.strm_content_template,
            thumb_template=req.thumb_template,
            poster_template=req.poster_template,
            fanart_template=req.fanart_template,
            extrafanart_template=req.extrafanart_template,
            nfo_template=req.nfo_template,
            trailer_template=req.trailer_template,
            subtitle_template=req.subtitle_template,
            subtitle_extensions=req.subtitle_extensions,
            write_nfo=req.write_nfo,
            copy_resources=req.copy_resources,
            trailer_pattern=req.trailer_pattern,
            blacklist_patterns=req.blacklist_patterns,
            min_file_size=req.min_file_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert lib.id is not None

    logger.info(
        "library created",
        library_id=lib.id,
        name=name,
        path=req.path,
        automation=req.automation,
        ingest=req.ingest,
    )

    if runtime.watcher_service:
        runtime.watcher_service.sync_library(lib)

    # 提交初始 Refresh 任务
    if req.scan:
        await repo.create_task(
            TaskType.REFRESH,
            RefreshPayload(
                library_id=lib.id,
                recursive=req.recursive,
                patterns=req.patterns,
                # 用落库后的路径: 入口已把库路径解析为真实路径, 首次扫描必须与它同一形式.
                path=lib.path,
                scan={ScanMode.add},
                scrape=set(),
            ),
        )

    return _library_response(lib)


@router.get("/{library_id}")
async def get_library(library_id: int, repo: RepoDep) -> LibraryResponse:
    lib = await repo.get_library(library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="媒体库不存在")
    return await _library_response_with_history(repo, lib)


@router.patch("/{library_id}")
async def update_library(
    library_id: int,
    req: LibraryUpdateRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> LibraryResponse:
    updates = cast("LibraryUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="没有需要修改的字段")

    # 仅 path 被显式更新时才校验
    if "path" in updates and updates["path"] is not None:
        await validate_directory_path(updates["path"], runtime.safe_dirs)

    try:
        lib = await repo.update_library(library_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if lib is None:
        raise HTTPException(status_code=404, detail="媒体库不存在")
    logger.info("library updated", library_id=library_id, fields=list(updates.keys()))

    watch_fields = {
        "automation",
        "ingest",
        "cloud_path",
        "path",
        "recursive",
        "patterns",
        "trailer_pattern",
        "blacklist_patterns",
        "min_file_size",
    }
    if runtime.watcher_service and watch_fields & updates.keys():
        runtime.watcher_service.sync_library(lib)
    # 清单记录生成时的库根: 路径改掉后按新库根重解释相对路径会删错文件.
    if "path" in updates:
        runtime.inventory_store.drop_library(library_id)

    return await _library_response_with_history(repo, lib)


@router.delete("/{library_id}", status_code=204)
async def delete_library(library_id: int, repo: RepoDep, runtime: RuntimeDep):
    """级联删除该库 MediaFile (library_id 非空 FK). 仅删除数据库索引, 不动磁盘文件."""
    existing = await repo.list_libraries()
    if not any(lib.id == library_id for lib in existing):
        raise HTTPException(status_code=404, detail="媒体库不存在")

    # 先停止监控, 避免删库后 watcher 仍为已删库 id 创建悬空 MediaFile
    if runtime.watcher_service:
        runtime.watcher_service.remove_library(library_id)

    deleted_media = await repo.delete_library(library_id)
    runtime.inventory_store.drop_library(library_id)
    logger.info("library deleted", library_id=library_id, deleted_media=deleted_media)
    return Response(status_code=204)
