from collections.abc import Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from ...db.models import SortOrder, Task, TaskSortField, TaskStatus, TaskType
from ...observability import build_record_zip
from ...utils.model import to_resp
from ..deps import ConfigDep, RepoDep, RuntimeDep
from ..models import (
    TaskChildItem,
    TaskChildListResponse,
    TaskChildStatusCounts,
    TaskListItem,
    TaskListResponse,
    TaskResponse,
    TaskSubmission,
)
from ..models.tasks import TaskBatchRequest, TaskBatchResponse, TaskWorkerResponse
from ..support.task_batch import execute_task_batch
from ..support.task_resolve import resolve_submission

logger = structlog.get_logger()

router = APIRouter(prefix="/tasks", tags=["tasks"])

# 标题取库名的任务类型: payload 带 `library_id` 的库内任务.
_LIBRARY_TASK_TYPES = (TaskType.REFRESH, TaskType.ORGANIZE, TaskType.SCAN_INVALID, TaskType.DELETE)


async def _task_titles(repo: RepoDep, tasks: Sequence[Task]) -> dict[int, str | None]:
    """scrape→番号, actor_scrape→演员名, 库内任务→库名. 批量查库, 避免列表 N+1."""
    actor_ids = {
        int(p["actor_id"])
        for t in tasks
        if t.type == TaskType.ACTOR_SCRAPE and isinstance((p := t.payload or {}).get("actor_id"), int)
    }
    library_ids = {
        int(p["library_id"])
        for t in tasks
        if t.type in _LIBRARY_TASK_TYPES and isinstance((p := t.payload or {}).get("library_id"), int)
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
        elif t.type in _LIBRARY_TASK_TYPES:
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


async def _decorate_tasks[T: TaskListItem](repo: RepoDep, tasks: Sequence[Task], model: type[T]) -> list[T]:
    """展示标题 + 直接后继计数. 一次批量, 避免列表 N+1.

    传 `TaskListItem` 得列表用的精简形状; 传 `TaskResponse` 时结果按判别联合校验.
    """
    titles = await _task_titles(repo, tasks)
    status_map = await repo.child_status_counts([t.id for t in tasks if t.id is not None])
    items: list[T] = []
    for t in tasks:
        child_status = _child_status(status_map.get(t.id) if t.id is not None else None)
        items.append(
            to_resp(model, t).model_copy(
                update={
                    "title": titles.get(t.id) if t.id is not None else None,
                    "child_count": child_status.queued + child_status.running + child_status.done + child_status.failed,
                    "child_status": child_status,
                }
            )
        )
    return items


async def _to_resp(repo: RepoDep, task: Task) -> TaskResponse:
    """详情: 结果按判别联合校验, 并核对成员类型与任务类型; 读不出来或不一致时置空."""
    resp = (await _decorate_tasks(repo, [task], TaskResponse))[0]
    if task.result is None:
        return resp
    # 缺 type 的行属预期状态 (每次展开都会命中), 降到 debug; 真的写坏了要靠记录目录排查.
    if resp.result is None:
        logger.debug("task result unreadable", task_id=task.id, task_type=str(task.type))
        return resp
    if resp.result.type != task.type:
        logger.debug(
            "task result type mismatch",
            task_id=task.id,
            task_type=str(task.type),
            result_type=str(resp.result.type),
        )
        return resp.model_copy(update={"result": None})
    return resp


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
    """默认只返回链根 (roots_only). 子任务由 /children 按需加载;
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
    return TaskListResponse(items=await _decorate_tasks(repo, items, TaskListItem), total=total)


@router.get("/{task_id}/children")
async def get_task_children(
    task_id: int,
    repo: RepoDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskChildListResponse:
    """直接后继 (TaskLink 出边). total 为出边总数, 不受本页截断."""
    if await repo.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    pairs = await repo.list_children(task_id, limit=limit, offset=offset)
    decorated = await _decorate_tasks(repo, [task for task, _ in pairs], TaskListItem)
    parent_counts = await repo.child_status_counts([task_id])
    total = sum(parent_counts.get(task_id, {}).values())
    return TaskChildListResponse(
        items=[
            TaskChildItem.model_validate({**resp.model_dump(), "link_key": key})
            for resp, (_, key) in zip(decorated, pairs, strict=True)
        ],
        total=total,
    )


@router.post("", status_code=202)
async def submit_task(req: Annotated[TaskSubmission, Body(...)], repo: RepoDep, runtime: RuntimeDep) -> TaskResponse:
    task_type, payload = await resolve_submission(req, repo, rules=runtime.number_rules)
    task = await repo.create_task(task_type=task_type, payload=payload)
    # mode="json": 该日志经 WS 广播时会被 json 序列化, payload 中的 set/enum 等需转为原生类型.
    logger.info("task submitted", task_id=task.id, task_type=task_type, payload=payload.model_dump(mode="json"))
    return await _to_resp(repo, task)


@router.get("/schema")
async def get_task_schema() -> dict:
    return TypeAdapter(TaskSubmission).json_schema()


@router.get("/worker")
async def get_task_worker(runtime: RuntimeDep) -> TaskWorkerResponse:
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
    """``task_ids`` 与 status/type 互斥."""
    result = await execute_task_batch(
        action=req.action,
        repo=repo,
        cancel_task=runtime.cancel_task,
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
    task = await repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return await _to_resp(repo, task)


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
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in (TaskStatus.DONE, TaskStatus.FAILED):
        raise HTTPException(status_code=409, detail="只有已结束的任务才能导出任务记录")

    try:
        data = build_record_zip(config.cold.log_dir, task_id, include_secrets=include_secrets)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="该任务没有任务记录") from None
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-record.zip"'},
    )
