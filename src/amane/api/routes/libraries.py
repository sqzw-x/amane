from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from fastapi import APIRouter, HTTPException, Response

from ...db.models import TaskType
from ...enums import LibraryAutomation
from ...handlers import RefreshPayload, ScanMode
from ...organize import path_template_schema
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    LibraryCreateRequest,
    LibraryListResponse,
    LibraryResponse,
    LibraryUpdateRequest,
    PathTemplateSchemaResponse,
)
from ..support.path_validation import validate_directory_path

if TYPE_CHECKING:
    from ...db.repo_types import LibraryUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("/path-template-schema")
async def get_path_template_schema() -> PathTemplateSchemaResponse:
    """路径模板占位符与默认值 (与 resolve_paths 同源, 供前端表单渲染)."""
    return PathTemplateSchemaResponse.model_validate(path_template_schema())


@router.get("")
async def list_libraries(repo: RepoDep) -> LibraryListResponse:
    """列出所有已配置的媒体库"""
    items = await repo.list_libraries()
    return LibraryListResponse(items=[to_resp(LibraryResponse, lib) for lib in items])


@router.post("", status_code=201)
async def create_library(req: LibraryCreateRequest, repo: RepoDep, runtime: RuntimeDep) -> LibraryResponse:
    """添加新的媒体库, 可选触发初始扫描并热添加到监控器"""
    # 路径校验: 必须存在, 是目录, 在 safe_dirs 内
    validate_directory_path(req.path, runtime.safe_dirs)

    name = req.name or Path(req.path).name

    lib = await repo.create_library(
        name=name,
        path=req.path,
        automation=req.automation,
        recursive=req.recursive,
        patterns=req.patterns,
        move_mode=req.move_mode,
        video_template=req.video_template,
        link_template=req.link_template,
        link_mode=req.link_mode,
        cd_suffix_template=req.cd_suffix_template,
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
    assert lib.id is not None

    logger.info("library created", library_id=lib.id, name=name, path=req.path, automation=req.automation)

    # 热添加到文件监控器
    if req.automation != LibraryAutomation.NONE and runtime.watcher_service:
        runtime.watcher_service.add_library(
            path=req.path,
            library_id=lib.id,
            recursive=req.recursive,
            patterns=req.patterns,
            skip_patterns=[req.trailer_pattern, *req.blacklist_patterns],
            min_file_size=req.min_file_size,
        )

    # 提交初始 Refresh 任务
    if req.scan:
        await repo.create_task(
            TaskType.REFRESH,
            RefreshPayload(
                library_id=lib.id,
                recursive=req.recursive,
                patterns=req.patterns,
                path=req.path,
                scan={ScanMode.add},
                scrape=set(),
            ),
        )

    return to_resp(LibraryResponse, lib)


@router.get("/{library_id}")
async def get_library(library_id: int, repo: RepoDep) -> LibraryResponse:
    lib = await repo.get_library(library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="Library not found")
    return to_resp(LibraryResponse, lib)


@router.patch("/{library_id}")
async def update_library(
    library_id: int,
    req: LibraryUpdateRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> LibraryResponse:
    """更新媒体库配置"""
    updates = cast("LibraryUpdates", req.model_dump(exclude_unset=True))
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    # 仅当 path 字段被显式更新时才校验 -- 其它字段更新不需要重新检查路径
    if "path" in updates and updates["path"] is not None:
        validate_directory_path(updates["path"], runtime.safe_dirs)

    lib = await repo.update_library(library_id, **updates)
    if lib is None:
        raise HTTPException(status_code=404, detail="Library not found")
    logger.info("library updated", library_id=library_id, fields=list(updates.keys()))

    # 同步文件监控: 监控相关字段变化时, 先移除旧监控再按最新状态重建
    watch_fields = {
        "automation",
        "path",
        "recursive",
        "patterns",
        "trailer_pattern",
        "blacklist_patterns",
        "min_file_size",
    }
    if runtime.watcher_service and watch_fields & updates.keys():
        runtime.watcher_service.remove_library(library_id)
        if lib.automation != LibraryAutomation.NONE:
            assert lib.id is not None
            runtime.watcher_service.add_library(
                path=lib.path,
                library_id=lib.id,
                recursive=lib.recursive,
                patterns=lib.patterns,
                skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                min_file_size=lib.min_file_size,
            )

    return to_resp(LibraryResponse, lib)


@router.delete("/{library_id}", status_code=204)
async def delete_library(library_id: int, repo: RepoDep, runtime: RuntimeDep):
    """删除媒体库.

    级联删除该库下所有 MediaFile 记录 (library_id 非空 FK), 并停止监控该目录.
    仅删除数据库索引, 不动磁盘文件.
    """
    existing = await repo.list_libraries()
    if not any(lib.id == library_id for lib in existing):
        raise HTTPException(status_code=404, detail="Library not found")

    # 先停止监控, 避免删库后 watcher 仍为已删库 id 创建悬空 MediaFile
    if runtime.watcher_service:
        runtime.watcher_service.remove_library(library_id)

    deleted_media = await repo.delete_library(library_id)
    logger.info("library deleted", library_id=library_id, deleted_media=deleted_media)
    return Response(status_code=204)
