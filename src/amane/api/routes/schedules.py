from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import structlog
from croniter import croniter
from fastapi import APIRouter, HTTPException, Response
from pydantic import TypeAdapter

from ...db.models import RoutineType
from ...utils.model import to_resp
from ..deps import RepoDep
from ..models import (
    RoutineSubmission,
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdateRequest,
)

if TYPE_CHECKING:
    from ...db.repo_types import ScheduleUpdates

logger = structlog.get_logger()

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(repo: RepoDep) -> ScheduleListResponse:
    items = await repo.list_schedules()
    return ScheduleListResponse(items=[to_resp(ScheduleResponse, s) for s in items], total=len(items))


@router.get("/schema")
async def get_schedule_schema() -> dict:
    return TypeAdapter(RoutineSubmission).json_schema()


@router.post("", status_code=201)
async def create_schedule(req: ScheduleCreateRequest, repo: RepoDep) -> ScheduleResponse:
    if not croniter.is_valid(req.cron):
        raise HTTPException(status_code=422, detail="Invalid cron expression")

    next_run = croniter(req.cron, datetime.now(UTC)).get_next(datetime)
    task_type = RoutineType(req.submission.type)
    payload = req.submission.model_dump(mode="json")

    schedule = await repo.create_schedule(
        name=req.name,
        cron=req.cron,
        task_type=task_type,
        payload=payload,
        enabled=req.enabled,
        next_run=next_run,
    )
    logger.info("schedule created", schedule_id=schedule.id, name=req.name, cron=req.cron, task_type=task_type)
    return to_resp(ScheduleResponse, schedule)


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: int, repo: RepoDep) -> ScheduleResponse:
    schedule = await repo.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return to_resp(ScheduleResponse, schedule)


@router.patch("/{schedule_id}")
async def update_schedule(schedule_id: int, req: ScheduleUpdateRequest, repo: RepoDep) -> ScheduleResponse:
    """仅 name/cron/enabled. 修改任务内容须删除后重建."""
    updates = cast("ScheduleUpdates", req.model_dump(exclude_unset=True))

    if "cron" in updates:
        if not croniter.is_valid(updates["cron"]):
            raise HTTPException(status_code=422, detail="Invalid cron expression")
        updates["next_run"] = croniter(updates["cron"], datetime.now(UTC)).get_next(datetime)

    schedule = await repo.update_schedule(schedule_id, **updates)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    logger.info("schedule updated", schedule_id=schedule_id, fields=list(updates.keys()))
    return to_resp(ScheduleResponse, schedule)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, repo: RepoDep):
    deleted = await repo.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    logger.info("schedule deleted", schedule_id=schedule_id)
    return Response(status_code=204)


@router.post("/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: int, repo: RepoDep) -> ScheduleResponse:
    """将 next_run 设置为当前时间, 由 cron 在下一个 tick 执行."""
    schedule = await repo.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    assert schedule.id is not None
    updated = await repo.update_schedule(schedule.id, next_run=datetime.now(UTC))
    assert updated is not None
    logger.info("schedule triggered manually", schedule_id=schedule_id)
    return to_resp(ScheduleResponse, updated)
