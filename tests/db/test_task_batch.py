"""execute_task_batch / cleanup_task_artifacts: 不经 FastAPI lifespan."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.api.models.tasks import TaskBatchAction
from amane.api.support.task_batch import cleanup_task_artifacts, execute_task_batch
from amane.db.models import TaskStatus, TaskType
from amane.scheduler.worker import CANCEL_ERROR

if TYPE_CHECKING:
    from amane.db.repository import Repository


def _cancel(*, ok: bool = True) -> Callable[[int], Awaitable[bool]]:
    async def cancel(task_id: int) -> bool:
        return ok

    return cancel


@pytest.mark.asyncio(loop_scope="function")
async def test_cleanup_task_artifacts_removes_log(repo: Repository, tmp_path: Path) -> None:
    t = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
    assert t.id is not None
    rel = f"tasks/task-{t.id}/task.log"
    await repo.update_task_log_file(t.id, rel)
    refreshed = await repo.get_task(t.id)
    assert refreshed is not None
    log_path = tmp_path / rel
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"event": "x"}\n', encoding="utf-8")
    cleanup_task_artifacts(refreshed, tmp_path)
    assert not log_path.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_delete_counts_and_skips_active_chain(repo: Repository, tmp_path: Path) -> None:
    ids: list[int] = []
    for _ in range(3):
        t = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert t.id is not None
        await repo.complete_task(t.id)
        ids.append(t.id)
    queued = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
    assert queued.id is not None
    body = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[*ids, queued.id, 999_999],
        statuses=None,
        task_types=None,
    )
    assert body.affected == 3
    assert body.skipped == 1
    assert body.missing == 1
    for tid in ids:
        assert await repo.get_task(tid) is None
    assert await repo.get_task(queued.id) is not None
    leftover = await repo.get_task(queued.id)
    assert leftover is not None
    await repo.fail_task(queued.id, error="drain")
    assert await repo.delete_tasks([queued.id]) == 1

    parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    await repo.complete_task_with_followups(
        claimed.id, result={}, followups=[("scrape:1", TaskType.SCRAPE, {"number": "LIVE"}, 0)]
    )
    lone = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
    assert lone.id is not None
    await repo.complete_task(lone.id)
    filtered = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=[TaskStatus.DONE],
        task_types=None,
    )
    assert filtered.affected == 1
    assert filtered.skipped >= 1
    assert await repo.get_task(lone.id) is None
    assert await repo.get_task(parent.id) is not None

    scrape = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
    refresh = await repo.create_task(task_type=TaskType.REFRESH, payload={})
    assert scrape.id is not None and refresh.id is not None
    await repo.complete_task(scrape.id)
    await repo.complete_task(refresh.id)
    by_type = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=None,
        task_types=[TaskType.SCRAPE],
    )
    assert by_type.affected == 1
    assert await repo.get_task(scrape.id) is None
    assert await repo.get_task(refresh.id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_delete_mixed_tree_keeps_failed_under_root(repo: Repository, tmp_path: Path) -> None:
    """按 status=done 清除时保留混合树的链根; 失败子仍挂在父下, 父日志不被删除."""
    parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
    assert parent.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == parent.id
    assert claimed.id is not None
    children = await repo.complete_task_with_followups(
        claimed.id,
        result={},
        followups=[
            ("scrape:ok", TaskType.SCRAPE, {"number": "OK"}, 0),
            ("scrape:bad", TaskType.SCRAPE, {"number": "BAD"}, 0),
        ],
    )
    done_child, failed_child = children
    assert done_child.id is not None and failed_child.id is not None
    await repo.complete_task(done_child.id)
    await repo.fail_task(failed_child.id, error="boom")

    rel = f"tasks/task-{parent.id}/task.log"
    await repo.update_task_log_file(parent.id, rel)
    log_path = tmp_path / rel
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"event": "parent"}\n', encoding="utf-8")

    cleared_done = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=[TaskStatus.DONE],
        task_types=None,
    )
    assert cleared_done.affected == 1
    assert await repo.get_task(done_child.id) is None
    assert await repo.get_task(parent.id) is not None
    assert await repo.get_task(failed_child.id) is not None
    assert log_path.is_file()
    listed = await repo.list_tasks(statuses=[TaskStatus.FAILED], roots_only=True)
    assert parent.id in {t.id for t in listed}

    cleared_failed = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=[TaskStatus.FAILED],
        task_types=None,
    )
    assert cleared_failed.affected == 1
    assert await repo.get_task(failed_child.id) is None
    assert await repo.get_task(parent.id) is not None

    cleared_empty = await execute_task_batch(
        action=TaskBatchAction.DELETE,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=[TaskStatus.DONE],
        task_types=None,
    )
    assert cleared_empty.affected == 1
    assert await repo.get_task(parent.id) is None
    assert not log_path.is_file()


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_cancel_queued_running_and_filter(repo: Repository, tmp_path: Path) -> None:
    queued = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
    assert queued.id is not None
    cancelled = await execute_task_batch(
        action=TaskBatchAction.CANCEL,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[queued.id],
        statuses=None,
        task_types=None,
    )
    assert cancelled.affected == 1
    updated = await repo.get_task(queued.id)
    assert updated is not None
    assert updated.status == TaskStatus.FAILED
    assert updated.error == CANCEL_ERROR

    done = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
    assert done.id is not None
    await repo.complete_task(done.id)
    skip = await execute_task_batch(
        action=TaskBatchAction.CANCEL,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[done.id],
        statuses=None,
        task_types=None,
    )
    assert skip.model_dump() == {"affected": 0, "skipped": 1, "missing": 0, "submitted": 0, "task_ids": []}

    missing = await execute_task_batch(
        action=TaskBatchAction.CANCEL,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[9999],
        statuses=None,
        task_types=None,
    )
    assert missing.missing == 1
    assert missing.affected == 0

    running = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
    assert running.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None and claimed.id == running.id
    fallback = await execute_task_batch(
        action=TaskBatchAction.CANCEL,
        repo=repo,
        cancel_task=_cancel(ok=False),
        log_dir=tmp_path,
        task_ids=[running.id],
        statuses=None,
        task_types=None,
    )
    assert fallback.affected == 1
    failed = await repo.get_task(running.id)
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert CANCEL_ERROR in (failed.error or "")

    a = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
    b = await repo.create_task(task_type=TaskType.REFRESH, payload={})
    assert a.id is not None and b.id is not None
    by_type = await execute_task_batch(
        action=TaskBatchAction.CANCEL,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=None,
        task_types=[TaskType.SCRAPE],
    )
    assert by_type.affected == 1
    ra = await repo.get_task(a.id)
    rb = await repo.get_task(b.id)
    assert ra is not None and ra.status == TaskStatus.FAILED
    assert rb is not None and rb.status == TaskStatus.QUEUED


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_retry(repo: Repository, tmp_path: Path) -> None:
    task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "MIDV-123"})
    assert task.id is not None
    await repo.fail_task(task.id, error="test failure")
    body = await execute_task_batch(
        action=TaskBatchAction.RETRY,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[task.id],
        statuses=None,
        task_types=None,
    )
    assert body.affected == 1
    assert body.submitted == 1
    assert len(body.task_ids) == 1
    new_id = body.task_ids[0]
    assert new_id != task.id
    created = await repo.get_task(new_id)
    assert created is not None
    assert created.status == TaskStatus.QUEUED
    assert created.payload == {"number": "MIDV-123"}
    await repo.fail_task(task.id, error="drain")  # 已是 FAILED, 删掉以免后面 status=failed 再命中
    assert await repo.delete_tasks([task.id]) == 1

    queued = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
    assert queued.id is not None
    skip = await execute_task_batch(
        action=TaskBatchAction.RETRY,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=[queued.id],
        statuses=None,
        task_types=None,
    )
    assert skip.skipped == 1
    assert skip.affected == 0

    a = await repo.create_task(task_type=TaskType.SCRAPE, payload={"n": 1})
    b = await repo.create_task(task_type=TaskType.SCRAPE, payload={"n": 2})
    assert a.id is not None and b.id is not None
    await repo.fail_task(a.id, error="x")
    await repo.complete_task(b.id)
    filtered = await execute_task_batch(
        action=TaskBatchAction.RETRY,
        repo=repo,
        cancel_task=_cancel(),
        log_dir=tmp_path,
        task_ids=None,
        statuses=[TaskStatus.FAILED],
        task_types=None,
    )
    assert filtered.affected == 1
    assert filtered.submitted == 1
