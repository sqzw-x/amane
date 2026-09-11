from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, model_validator

from ...db import RoutineType, Schedule
from ...utils.model import create_partial_model
from .tasks import RoutineSubmission


class ScheduleCreateRequest(BaseModel):
    name: str | None = None
    cron: str
    enabled: bool = True
    submission: RoutineSubmission


if TYPE_CHECKING:
    type ScheduleUpdateRequest = Schedule

# 外部可写面: 仅 name/cron/enabled; 修改 task_type/payload 须删除后重建, last_run/next_run 由调度器维护, id 只读.
ScheduleUpdateRequest = create_partial_model(
    Schedule, fields=("name", "cron", "enabled"), partial_cls_name="ScheduleUpdateRequest"
)


class ScheduleResponse(BaseModel):
    id: int
    name: str | None = None
    cron: str
    task_type: RoutineType
    payload: RoutineSubmission
    enabled: bool
    last_run: datetime | None = None
    next_run: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _payload_type_from_task(cls, data: object) -> object:
        """JSON 列缺 discriminator 时用 task_type; 缺字段随后由 RoutineSubmission 默认值补齐."""
        if not isinstance(data, dict):
            return data
        task_type = data.get("task_type")
        payload = data.get("payload")
        if task_type is None or isinstance(payload, BaseModel):
            return data
        if payload is not None and not isinstance(payload, dict):
            return data
        return {**data, "payload": {**(payload or {}), "type": RoutineType(task_type)}}


class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int
