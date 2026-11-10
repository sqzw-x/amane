"""任务状态迁移的条件更新: 并发只有一方生效, 终态不被覆盖."""

import asyncio

import pytest

from amane.db.models import TaskStatus, TaskType
from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_claim_winner_is_unique(repo: Repository) -> None:
    task = await repo.create_task(TaskType.SCRAPE, {})
    assert task.id is not None

    results = await asyncio.gather(repo.claim_next_task(), repo.claim_next_task())

    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    assert winners[0].id == task.id
    assert winners[0].status is TaskStatus.RUNNING


@pytest.mark.asyncio(loop_scope="function")
async def test_completion_and_cancel_only_one_wins(repo: Repository) -> None:
    task = await repo.create_task(TaskType.SCRAPE, {})
    assert task.id is not None
    await repo.claim_next_task()

    children, cancelled = await asyncio.gather(
        repo.complete_task_with_followups(task.id, {}, [("child", TaskType.SCRAPE, {}, 0)]),
        repo.fail_running_task(task.id, error="cancelled"),
    )

    final = await repo.get_task(task.id)
    assert final is not None
    links = await repo.list_task_links(parent_task_id=task.id)
    if cancelled:
        assert children == []
        assert final.status is TaskStatus.FAILED
        assert links == []
    else:
        assert len(children) == 1
        assert final.status is TaskStatus.DONE
        assert len(links) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_task_only_accepts_running(repo: Repository) -> None:
    task = await repo.create_task(TaskType.SCRAPE, {})
    assert task.id is not None

    assert await repo.complete_task(task.id) is False
    queued = await repo.get_task(task.id)
    assert queued is not None
    assert queued.status is TaskStatus.QUEUED

    await repo.claim_next_task()
    assert await repo.complete_task(task.id) is True
    assert await repo.complete_task(task.id) is False


@pytest.mark.asyncio(loop_scope="function")
async def test_fail_task_accepts_active_states_only(repo: Repository) -> None:
    queued = await repo.create_task(TaskType.SCRAPE, {})
    assert queued.id is not None
    assert await repo.fail_task(queued.id, error="queued boom") is True
    assert await repo.fail_task(queued.id, error="again") is False

    running = await repo.create_task(TaskType.SCRAPE, {})
    assert running.id is not None
    await repo.claim_next_task()
    assert await repo.fail_task(running.id, error="running boom") is True

    assert await repo.fail_running_task(queued.id, error="terminal") is False


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_task_with_followups_requires_running(repo: Repository) -> None:
    task = await repo.create_task(TaskType.SCRAPE, {})
    assert task.id is not None
    followups: list[tuple[str, TaskType, dict[str, object], int]] = [("child", TaskType.SCRAPE, {"number": "A"}, 0)]

    assert await repo.complete_task_with_followups(task.id, {}, followups) == []
    assert await repo.list_task_links(parent_task_id=task.id) == []

    await repo.claim_next_task()
    children = await repo.complete_task_with_followups(task.id, {}, followups)
    assert len(children) == 1
    assert await repo.list_task_links(parent_task_id=task.id) != []

    parent = await repo.get_task(task.id)
    assert parent is not None
    assert parent.status is TaskStatus.DONE
    assert parent.root_task_id == task.id
    assert children[0].root_task_id == task.id
