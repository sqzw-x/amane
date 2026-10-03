"""按 ID 或与列表同形的 status/type 筛选."""

import contextlib
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from ...db.models import Task, TaskStatus, TaskType
from ...db.repository import Repository
from ...observability import remove_task_dir
from ...scheduler.worker import CANCEL_ERROR
from ..models.tasks import TaskBatchAction, TaskBatchResponse

CancelRunning = Callable[[int], Awaitable[bool]]

_CANCELABLE = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})
_DELETABLE = frozenset({TaskStatus.DONE, TaskStatus.FAILED})
_RETRYABLE = frozenset({TaskStatus.FAILED})

_ACTION_STATUSES: dict[TaskBatchAction, frozenset[TaskStatus]] = {
    TaskBatchAction.CANCEL: _CANCELABLE,
    TaskBatchAction.DELETE: _DELETABLE,
    TaskBatchAction.RETRY: _RETRYABLE,
}


def _intersect_statuses(requested: Sequence[TaskStatus] | None, allowed: frozenset[TaskStatus]) -> list[TaskStatus]:
    if requested is None:
        return list(allowed)
    return [status for status in requested if status in allowed]


def cleanup_task_artifacts(task: Task, log_dir: Path) -> None:
    if task.id is not None:
        remove_task_dir(log_dir, task.id)
    if task.log_file:
        log_path = log_dir / task.log_file
        if log_path.is_file():
            with contextlib.suppress(OSError):
                log_path.unlink(missing_ok=True)


async def _cancel_running(cancel_task: CancelRunning, repo: Repository, tasks: Sequence[Task]) -> int:
    """取消失败的记录为 failed (CANCEL_ERROR); 不覆盖已终态."""
    affected = 0
    for task in tasks:
        if task.id is None:
            continue
        cancelled = await cancel_task(task.id)
        if cancelled or await repo.fail_running_task(task.id, error=CANCEL_ERROR):
            affected += 1
    return affected


async def execute_task_batch(
    *,
    action: TaskBatchAction,
    repo: Repository,
    cancel_task: CancelRunning,
    log_dir: Path,
    task_ids: Sequence[int] | None,
    statuses: Sequence[TaskStatus] | None,
    task_types: Sequence[TaskType] | None,
) -> TaskBatchResponse:
    """有 ``task_ids`` 则精确匹配, 否则按状态/类型筛选; CANCEL 区分 queued/running."""
    allowed = _ACTION_STATUSES[action]
    if task_ids is not None:
        unique_ids = list(dict.fromkeys(task_ids))
        found = await repo.find_tasks(task_ids=unique_ids)
        missing = len(unique_ids) - len(found)
        return await _apply_found(
            action=action,
            repo=repo,
            cancel_task=cancel_task,
            log_dir=log_dir,
            found=found,
            missing=missing,
        )

    effective = _intersect_statuses(statuses, allowed)
    if not effective:
        return TaskBatchResponse()

    # CANCEL 区分 queued / running
    if action == TaskBatchAction.CANCEL:
        queued_n = 0
        if TaskStatus.QUEUED in effective:
            queued_n = await repo.fail_queued_tasks(error=CANCEL_ERROR, task_types=task_types)
        running: list[Task] = []
        if TaskStatus.RUNNING in effective:
            running = await repo.find_tasks(statuses=[TaskStatus.RUNNING], task_types=task_types)
        running_n = await _cancel_running(cancel_task, repo, running) if running else 0
        return TaskBatchResponse(affected=queued_n + running_n)

    found = await repo.find_tasks(statuses=effective, task_types=task_types)
    return await _apply_found(
        action=action,
        repo=repo,
        cancel_task=cancel_task,
        log_dir=log_dir,
        found=found,
        missing=0,
    )


async def _apply_found(
    *,
    action: TaskBatchAction,
    repo: Repository,
    cancel_task: CancelRunning,
    log_dir: Path,
    found: Sequence[Task],
    missing: int,
) -> TaskBatchResponse:
    allowed = _ACTION_STATUSES[action]
    eligible = [task for task in found if task.status in allowed]
    skipped = len(found) - len(eligible)

    if action == TaskBatchAction.CANCEL:
        queued_ids = [task.id for task in eligible if task.status == TaskStatus.QUEUED and task.id is not None]
        running = [task for task in eligible if task.status == TaskStatus.RUNNING]
        affected = 0
        if queued_ids:
            affected += await repo.fail_queued_tasks(error=CANCEL_ERROR, task_ids=queued_ids)
        if running:
            affected += await _cancel_running(cancel_task, repo, running)
        return TaskBatchResponse(affected=affected, skipped=skipped, missing=missing)

    if action == TaskBatchAction.DELETE:
        ids = [task.id for task in eligible if task.id is not None]
        deleted = await repo.delete_tasks(ids)
        leftover_ids: set[int] = set()
        if deleted and deleted < len(ids):
            leftover_ids = {task.id for task in await repo.find_tasks(task_ids=ids) if task.id is not None}
        if deleted:
            for task in eligible:
                if task.id is not None and task.id not in leftover_ids:
                    cleanup_task_artifacts(task, log_dir)
        # 有集合外后裔的行被跳过, 计入 skipped (与 status 不合格的 skipped 相加).
        return TaskBatchResponse(affected=deleted, skipped=skipped + (len(eligible) - deleted), missing=missing)

    created = await repo.retry_tasks(eligible)
    new_ids = [task.id for task in created if task.id is not None]
    return TaskBatchResponse(
        affected=len(new_ids),
        skipped=skipped,
        missing=missing,
        submitted=len(new_ids),
        task_ids=new_ids,
    )
