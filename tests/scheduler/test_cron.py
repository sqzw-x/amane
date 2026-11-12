"""定时任务: 到期的 SEMANTIC_INDEX 例行提交对应任务, 载荷原样转换."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from amane.db import RoutineType, TaskType
from amane.handlers import SemanticIndexPayload
from amane.scheduler.cron import CronScheduler

if TYPE_CHECKING:
    from amane.db.repository import Repository


@pytest.mark.asyncio
async def test_due_semantic_schedule_enqueues_index_task(repo: Repository) -> None:
    schedule = await repo.create_schedule(
        cron="0 3 * * *",
        task_type=RoutineType.SEMANTIC_INDEX,
        payload={"type": "semantic_index", "limit": 50, "force": True},
        next_run=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert schedule.id is not None

    await CronScheduler(repo)._tick()

    tasks = await repo.list_tasks(task_types=[TaskType.SEMANTIC_INDEX])
    assert len(tasks) == 1
    payload = SemanticIndexPayload.model_validate(tasks[0].payload)
    assert (payload.limit, payload.force) == (50, True)
    updated = await repo.get_schedule(schedule.id)
    assert updated is not None and updated.last_run is not None


@pytest.mark.asyncio
async def test_future_semantic_schedule_stays_idle(repo: Repository) -> None:
    await repo.create_schedule(
        cron="0 3 * * *",
        task_type=RoutineType.SEMANTIC_INDEX,
        payload={"type": "semantic_index"},
        next_run=datetime.now(UTC) + timedelta(hours=1),
    )

    await CronScheduler(repo)._tick()

    assert await repo.list_tasks(task_types=[TaskType.SEMANTIC_INDEX]) == []
