from collections.abc import Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from ...db.models import SortOrder, Task, TaskSortField, TaskStatus, TaskType
from ...observability import build_record_zip, build_task_report
from ...observability.report import TaskReport
from ...utils.model import to_resp
from ..deps import ConfigDep, RepoDep, RuntimeDep
from ..models import (
    TaskChildListResponse,
    TaskChildResponse,
    TaskChildStatusCounts,
    TaskListResponse,
    TaskResponse,
    TaskSubmission,
)
from ..models.tasks import TaskBatchRequest, TaskBatchResponse, TaskWorkerResponse
from ..support.task_batch import execute_task_batch
from ..support.task_resolve import resolve_submission

logger = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _task_titles(repo: RepoDep, tasks: Sequence[Task]) -> dict[int, str | None]:
    """从 payload 派生展示标题: scrape→番号, actor_scrape→演员名, refresh/organize→库名.

    批量一次查库 (actor/library 名), 避免列表页 N+1.
    """
    actor_ids = {
        int(p["actor_id"])
        for t in tasks
        if t.type == TaskType.ACTOR_SCRAPE and isinstance((p := t.payload or {}).get("actor_id"), int)
    }
    library_ids = {
        int(p["library_id"])
        for t in tasks
        if t.type in (TaskType.REFRESH, TaskType.ORGANIZE) and isinstance((p := t.payload or {}).get("library_id"), int)
    }
    actor_names = await repo.get_actor_names(list(actor_ids))
    library_names = await repo.get_library_names(list(library_ids))
    out: dict[int, str | None] = {}
    for t in tasks:
        if t.id is None:
            continue
        payload = t.payload or {}
        title: str | None = None
        if t.type == TaskType.SCRAPE:
            number = payload.get("number")
            title = str(number) if number else None
        elif t.type == TaskType.ACTOR_SCRAPE:
            actor_id = payload.get("actor_id")
            title = actor_names.get(int(actor_id)) if isinstance(actor_id, int) else None
        elif t.type in (TaskType.REFRESH, TaskType.ORGANIZE):
            library_id = payload.get("library_id")
            title = library_names.get(int(library_id)) if isinstance(library_id, int) else None
        out[t.id] = title
    return out


def _child_status(counts: dict[TaskStatus, int] | None) -> TaskChildStatusCounts:
    counts = counts or {}
    return TaskChildStatusCounts(
        queued=counts.get(TaskStatus.QUEUED, 0),
        running=counts.get(TaskStatus.RUNNING, 0),
        done=counts.get(TaskStatus.DONE, 0),
        failed=counts.get(TaskStatus.FAILED, 0),
    )


async def _decorate_tasks(repo: RepoDep, tasks: Sequence[Task]) -> list[TaskResponse]:
    """Task → TaskResponse: 展示标题 + 直接后继计数. 一次批量, 避免列表 N+1."""
    titles = await _task_titles(repo, tasks)
    status_map = await repo.child_status_counts([t.id for t in tasks if t.id is not None])
    items: list[TaskResponse] = []
    for t in tasks:
        child_status = _child_status(status_map.get(t.id) if t.id is not None else None)
        items.append(
            to_resp(TaskResponse, t).model_copy(
                update={
                    "title": titles.get(t.id) if t.id is not None else None,
                    "child_count": child_status.queued + child_status.running + child_status.done + child_status.failed,
                    "child_status": child_status,
                }
            )
        )
    return items


async def _to_resp(repo: RepoDep, task: Task) -> TaskResponse:
    """Task → TaskResponse (含展示标题与后继计数)."""
    return (await _decorate_tasks(repo, [task]))[0]


@router.get("")
async def list_tasks(
    repo: RepoDep,
    status: Annotated[list[TaskStatus] | None, Query(description="Filter by task status (multiple)")] = None,
    type: Annotated[list[TaskType] | None, Query(description="Filter by task type (multiple)")] = None,
    root_task_id: Annotated[
        int | None, Query(description="Filter to a task chain (all tasks sharing the root)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Annotated[TaskSortField, Query(description="Sort field")] = TaskSortField.CREATED_AT,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.DESC,
) -> TaskListResponse:
    """列出任务. 默认只返回链根任务 (顶层), 子任务挂在父任务下由 /children 按需加载;
    显式 root_task_id 时返回该链全部任务."""
    items = (
        await repo.list_tasks_by_root(root_task_id)
        if root_task_id is not None
        else await repo.list_tasks(
            statuses=status, task_types=type, limit=limit, offset=offset, sort_by=sort_by, order=order, roots_only=True
        )
    )
    total = (
        len(items)
        if root_task_id is not None
        else await repo.count_tasks(statuses=status, task_types=type, roots_only=True)
    )
    return TaskListResponse(items=await _decorate_tasks(repo, items), total=total)


@router.get("/{task_id}/children")
async def get_task_children(
    task_id: int,
    repo: RepoDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskChildListResponse:
    """任务的直接后继子任务 (TaskLink 出边), 供树视图展开. total 为出边总数, 不受本页截断.

    每条带 link_key (父内后继语义键).
    """
    if await repo.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    pairs = await repo.list_children(task_id, limit=limit, offset=offset)
    decorated = await _decorate_tasks(repo, [task for task, _ in pairs])
    parent_counts = await repo.child_status_counts([task_id])
    total = sum(parent_counts.get(task_id, {}).values())
    return TaskChildListResponse(
        items=[
            TaskChildResponse.model_validate({**resp.model_dump(), "link_key": key})
            for resp, (_, key) in zip(decorated, pairs, strict=True)
        ],
        total=total,
    )


@router.post("", status_code=202)
async def submit_task(req: Annotated[TaskSubmission, Body(...)], repo: RepoDep) -> TaskResponse:
    """统一任务提交入口"""
    task_type, payload = await resolve_submission(req, repo)
    task = await repo.create_task(task_type=task_type, payload=payload)
    # mode="json": 该日志经 WS 广播时会被 json 序列化, payload 中的 set/enum 等需转为原生类型.
    logger.info("task submitted", task_id=task.id, task_type=task_type, payload=payload.model_dump(mode="json"))
    return await _to_resp(repo, task)


@router.get("/schema")
async def get_task_schema() -> dict:
    """
    返回任务提交表单的 JSON Schema, 适用于动态表单渲染.

    包含所有支持任务类型的 schema.
    """
    return TypeAdapter(TaskSubmission).json_schema()


@router.get("/worker")
async def get_task_worker(runtime: RuntimeDep) -> TaskWorkerResponse:
    """任务执行器是否暂停领队."""
    return TaskWorkerResponse(paused=runtime.worker.is_paused)


@router.post("/worker/pause")
async def pause_task_worker(runtime: RuntimeDep) -> TaskWorkerResponse:
    runtime.worker.set_paused(True)
    return TaskWorkerResponse(paused=True)


@router.post("/worker/resume")
async def resume_task_worker(runtime: RuntimeDep) -> TaskWorkerResponse:
    runtime.worker.set_paused(False)
    return TaskWorkerResponse(paused=False)


@router.post("/batch")
async def batch_tasks(
    req: Annotated[TaskBatchRequest, Body(...)],
    repo: RepoDep,
    runtime: RuntimeDep,
    config: ConfigDep,
) -> TaskBatchResponse:
    """按 ID 或与列表同形的 status/type 筛选, 批量 cancel / delete / retry."""
    result = await execute_task_batch(
        action=req.action,
        repo=repo,
        worker=runtime.worker,
        log_dir=config.cold.log_dir,
        task_ids=req.task_ids,
        statuses=req.status,
        task_types=req.type,
    )
    logger.info(
        "tasks batch",
        action=req.action,
        affected=result.affected,
        skipped=result.skipped,
        missing=result.missing,
        submitted=result.submitted,
    )
    return result


@router.get("/{task_id}")
async def get_task(task_id: int, repo: RepoDep) -> TaskResponse:
    """根据 ID 获取任务"""
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return await _to_resp(repo, task)


@router.get("/{task_id}/report")
async def get_task_report(task_id: int, repo: RepoDep, config: ConfigDep) -> TaskReport:
    """任务结果摘要 (面向 UI 的投影, 非完整记录导出). 仅终态可用."""
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.DONE, TaskStatus.FAILED):
        raise HTTPException(status_code=409, detail="Report is only available for finished tasks")
    return build_task_report(config.cold.log_dir, task)


@router.get("/{task_id}/record")
async def get_task_record(
    task_id: int,
    repo: RepoDep,
    config: ConfigDep,
    include_secrets: Annotated[
        bool, Query(description="Include plaintext cookies/tokens from local secrets snapshot")
    ] = False,
):
    """导出任务记录 (zip). 默认脱敏; include_secrets=true 需本地存在 .secrets.hot.json."""
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.DONE, TaskStatus.FAILED):
        raise HTTPException(status_code=409, detail="Record is only available for finished tasks")

    try:
        data = build_record_zip(config.cold.log_dir, task_id, include_secrets=include_secrets)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Record not found for this task") from None
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-record.zip"'},
    )
