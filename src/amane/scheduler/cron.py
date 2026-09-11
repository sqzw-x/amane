"""到期 Schedule 创建对应 Task, 并更新 last_run / next_run."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from croniter import croniter
from structlog.contextvars import bound_contextvars

from ..db import RoutineType, TaskType
from ..handlers import CleanupPayload, R18ImportPayload, RescrapePayload, UpscalePayload

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()

_CHECK_INTERVAL = 60.0


class CronScheduler:
    def __init__(self, repo: Repository):
        self._repo = repo
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        logger.info("cron scheduler started", interval=_CHECK_INTERVAL)

        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("cron scheduler tick error")
            await asyncio.sleep(_CHECK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        logger.info("cron scheduler stopping")

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        schedules = await self._repo.list_schedules()

        for schedule in schedules:
            if not schedule.enabled:
                continue
            if schedule.id is None:
                continue

            if schedule.next_run is None:
                # 尚无 next_run: 只写入下次触发时间
                next_run = croniter(schedule.cron, now).get_next(datetime)
                await self._repo.update_schedule(schedule.id, next_run=next_run)
            else:
                # 无时区则按 UTC
                next_run = schedule.next_run if schedule.next_run.tzinfo else schedule.next_run.replace(tzinfo=UTC)

            if next_run > now:
                continue

            with bound_contextvars(schedule_id=schedule.id, schedule_name=schedule.name, task_type=schedule.task_type):
                logger.info("schedule triggered", payload=schedule.payload)
                await self._execute_task(schedule.id, schedule.task_type, schedule.payload)

            new_next_run = croniter(schedule.cron, now).get_next(datetime)
            await self._repo.update_schedule(schedule.id, last_run=now, next_run=new_next_run)

    async def _execute_task(self, schedule_id: int, task_type: RoutineType, payload: dict) -> None:
        match task_type:
            case RoutineType.CLEANUP:
                await self._repo.create_task(
                    TaskType.CLEANUP,
                    CleanupPayload(
                        remove_missing_files=payload.get("remove_missing_files", True),
                        remove_unreferenced_resources=payload.get("remove_unreferenced_resources", True),
                    ),
                )
            case RoutineType.UPSCALE:
                await self._repo.create_task(
                    TaskType.UPSCALE,
                    UpscalePayload(
                        max_dim_threshold=payload.get("max_dim_threshold"),
                        max_bytes_threshold=payload.get("max_bytes_threshold"),
                        limit=payload.get("limit", 200),
                    ),
                )
            case RoutineType.R18_IMPORT:
                await self._repo.create_task(TaskType.R18_IMPORT, R18ImportPayload(force=payload.get("force", False)))
            case RoutineType.RESCRAPE:
                await self._repo.create_task(TaskType.RESCRAPE, RescrapePayload.model_validate(payload))
            case _:
                logger.warning("unknown task type", task_type=task_type)
