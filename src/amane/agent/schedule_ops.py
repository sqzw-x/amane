from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from croniter import croniter
from pydantic import BaseModel, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.models.schedules import ScheduleResponse
from ..db.models import RoutineType, Schedule
from ..db.repo_types import ScheduleUpdates
from ..utils.model import to_resp
from .payload_schema import ROUTINE_SUBMISSION, RoutineSubmissionType
from .tools import TOOL_OK, AgentDeps, require_approval, trace_tool


class AgentScheduleUpdate(BaseModel):
    """只处理显式传入的字段."""

    name: str | None = None
    cron: str | None = None
    enabled: bool | None = None


class ScheduleSummary(BaseModel):
    """列表视图 (`list_schedules`): 不带 routine payload, 细节走 get_schedule."""

    id: int
    name: str | None
    cron: str
    task_type: RoutineType
    enabled: bool
    next_run: datetime | None


def _schedule_info(schedule: Schedule) -> ScheduleResponse:
    return to_resp(ScheduleResponse, schedule)


def _schedule_summary(schedule: Schedule) -> ScheduleSummary:
    assert schedule.id is not None
    return ScheduleSummary(
        id=schedule.id,
        name=schedule.name,
        cron=schedule.cron,
        task_type=schedule.task_type,
        enabled=schedule.enabled,
        next_run=schedule.next_run,
    )


def build_schedule_ops_capability() -> Capability[AgentDeps]:

    cap: Capability[AgentDeps] = Capability(
        id="schedule-ops",
        instructions=(
            "Changing the routine type or payload requires deleting the schedule and creating a new "
            "one: update_schedule only accepts name / cron / enabled."
        ),
    )

    @cap.tool
    async def list_schedules(ctx: RunContext[AgentDeps]) -> dict[str, object]:
        """List all routine schedules."""
        trace_tool(ctx, "tool_call", {"tool": "list_schedules"})
        schedules = await ctx.deps.repo.list_schedules()
        result: dict[str, object] = {
            "items": [_schedule_summary(schedule).model_dump(mode="json") for schedule in schedules]
        }
        trace_tool(ctx, "tool_result", {"tool": "list_schedules", "result": result})
        return result

    @cap.tool
    async def get_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> dict[str, object]:
        """Get one routine schedule by id."""
        trace_tool(ctx, "tool_call", {"tool": "get_schedule", "schedule_id": schedule_id})
        schedule = await ctx.deps.repo.get_schedule(schedule_id)
        if schedule is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        result = _schedule_info(schedule).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "get_schedule", "result": result})
        return result

    @cap.tool
    async def get_routine_submission_schema(
        ctx: RunContext[AgentDeps], submission_type: RoutineSubmissionType
    ) -> dict[str, Any]:
        """Return the accepted fields for one routine submission type."""
        trace_tool(ctx, "tool_call", {"tool": "get_routine_submission_schema", "type": submission_type})
        out = ROUTINE_SUBMISSION.schema(submission_type)
        trace_tool(ctx, "tool_result", {"tool": "get_routine_submission_schema", "type": submission_type})
        return out

    @cap.tool
    async def create_schedule(
        ctx: RunContext[AgentDeps],
        cron: str,
        submission: dict[str, Any],
        name: str | None = None,
        enabled: bool = True,
    ) -> dict[str, object]:
        """Create a routine schedule; the `submission` shape comes from get_routine_submission_schema."""
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "create_schedule", "cron": cron, "submission": submission, "name": name, "enabled": enabled},
        )
        if not croniter.is_valid(cron):
            return {"error": "Invalid cron expression"}
        try:
            routine = ROUTINE_SUBMISSION.validate(submission)
        except ValidationError as exc:
            return ROUTINE_SUBMISSION.error(submission, exc)
        next_run = croniter(cron, datetime.now(UTC)).get_next(datetime)
        task_type = RoutineType(routine.type)
        schedule = await ctx.deps.repo.create_schedule(
            name=name,
            cron=cron,
            task_type=task_type,
            payload=routine.model_dump(mode="json"),
            enabled=enabled,
            next_run=next_run,
        )
        assert schedule.id is not None
        result: dict[str, object] = {"schedule_id": schedule.id}
        trace_tool(ctx, "tool_result", {"tool": "create_schedule", "result": result})
        return result

    @cap.tool
    async def update_schedule(
        ctx: RunContext[AgentDeps], schedule_id: int, patch: AgentScheduleUpdate
    ) -> str | dict[str, object]:
        """Patch schedule name, cron, or enabled state."""
        trace_tool(
            ctx,
            "tool_call",
            {"tool": "update_schedule", "schedule_id": schedule_id, "patch": patch.model_dump(mode="json")},
        )
        if await ctx.deps.repo.get_schedule(schedule_id) is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        if not patch.model_fields_set:
            return {"error": "patch 为空"}

        updates: dict[str, object] = {}
        if "name" in patch.model_fields_set:
            updates["name"] = patch.name
        if "enabled" in patch.model_fields_set:
            if patch.enabled is None:
                return {"error": "enabled 不能为 null"}
            updates["enabled"] = patch.enabled
        if "cron" in patch.model_fields_set:
            if patch.cron is None:
                return {"error": "cron 不能为 null"}
            if not croniter.is_valid(patch.cron):
                return {"error": "Invalid cron expression"}
            updates["cron"] = patch.cron
            updates["next_run"] = croniter(patch.cron, datetime.now(UTC)).get_next(datetime)

        updated = await ctx.deps.repo.update_schedule(schedule_id, **cast(ScheduleUpdates, updates))
        if updated is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "update_schedule", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def trigger_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> str | dict[str, object]:
        """Mark a schedule due for execution on the next CronScheduler tick."""
        trace_tool(ctx, "tool_call", {"tool": "trigger_schedule", "schedule_id": schedule_id})
        schedule = await ctx.deps.repo.get_schedule(schedule_id)
        if schedule is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        assert schedule.id is not None
        updated = await ctx.deps.repo.update_schedule(schedule.id, next_run=datetime.now(UTC))
        if updated is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "trigger_schedule", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def delete_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> str | dict[str, object]:
        """Delete a routine schedule."""
        detail = f"删除定时任务 id={schedule_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_schedule", "schedule_id": schedule_id})
        require_approval(ctx, sql=detail, tool="delete_schedule", extra={"schedule_id": schedule_id})
        if not await ctx.deps.repo.delete_schedule(schedule_id):
            return {"error": f"schedule {schedule_id} 不存在"}
        trace_tool(ctx, "tool_result", {"tool": "delete_schedule", "result": TOOL_OK})
        return TOOL_OK

    return cap
