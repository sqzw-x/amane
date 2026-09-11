from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from croniter import croniter
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.models.schedules import ScheduleListResponse, ScheduleResponse
from ..api.models.tasks import RoutineSubmission
from ..db.models import RoutineType, Schedule
from ..db.repo_types import ScheduleUpdates
from ..utils.model import to_resp
from .tools import AgentDeps, require_approval, trace_tool


class AgentScheduleCreate(BaseModel):
    name: str | None = None
    cron: str
    enabled: bool = True
    submission: RoutineSubmission

    model_config = {"str_strip_whitespace": True}


class AgentScheduleUpdate(BaseModel):
    """只处理显式传入的字段."""

    name: str | None = None
    cron: str | None = None
    enabled: bool | None = None


def _schedule_info(schedule: Schedule) -> ScheduleResponse:
    return to_resp(ScheduleResponse, schedule)


def build_schedule_ops_capability() -> Capability[AgentDeps]:

    cap: Capability[AgentDeps] = Capability(
        id="schedule-ops",
        description=(
            "Use for managing routine schedules: cleanup, upscale, r18_import, and rescrape. "
            "Create, update, trigger, inspect, list, or delete schedules."
        ),
        instructions=(
            "Schedule updates only change name, cron, and enabled. "
            "To change the routine type or payload, delete and recreate the schedule. "
            "trigger_schedule only makes the schedule due; CronScheduler creates the actual task "
            "on its next tick, so it is not synchronous execution. Deleting a schedule requires approval."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def list_schedules(ctx: RunContext[AgentDeps]) -> dict[str, object]:
        """List all routine schedules."""
        trace_tool(ctx, "tool_call", {"tool": "list_schedules"})
        schedules = await ctx.deps.repo.list_schedules()
        out = ScheduleListResponse(items=[_schedule_info(schedule) for schedule in schedules], total=len(schedules))
        result = out.model_dump(mode="json")
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
    async def create_schedule(ctx: RunContext[AgentDeps], request: AgentScheduleCreate) -> dict[str, object]:
        """Create a routine schedule."""
        trace_tool(ctx, "tool_call", {"tool": "create_schedule", "request": request.model_dump(mode="json")})
        if not croniter.is_valid(request.cron):
            return {"error": "Invalid cron expression"}
        next_run = croniter(request.cron, datetime.now(UTC)).get_next(datetime)
        task_type = RoutineType(request.submission.type)
        schedule = await ctx.deps.repo.create_schedule(
            name=request.name,
            cron=request.cron,
            task_type=task_type,
            payload=request.submission.model_dump(mode="json"),
            enabled=request.enabled,
            next_run=next_run,
        )
        result = _schedule_info(schedule).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "create_schedule", "result": result})
        return result

    @cap.tool
    async def update_schedule(
        ctx: RunContext[AgentDeps], schedule_id: int, patch: AgentScheduleUpdate
    ) -> dict[str, object]:
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
        result = _schedule_info(updated).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "update_schedule", "result": result})
        return result

    @cap.tool
    async def trigger_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> dict[str, object]:
        """Mark a schedule due for execution on the next CronScheduler tick."""
        trace_tool(ctx, "tool_call", {"tool": "trigger_schedule", "schedule_id": schedule_id})
        schedule = await ctx.deps.repo.get_schedule(schedule_id)
        if schedule is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        assert schedule.id is not None
        updated = await ctx.deps.repo.update_schedule(schedule.id, next_run=datetime.now(UTC))
        if updated is None:
            return {"error": f"schedule {schedule_id} 不存在"}
        result = _schedule_info(updated).model_dump(mode="json")
        trace_tool(ctx, "tool_result", {"tool": "trigger_schedule", "result": result})
        return result

    @cap.tool
    async def delete_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> dict[str, object]:
        """Delete a routine schedule. Requires user approval."""
        detail = f"删除定时任务 id={schedule_id}"
        trace_tool(ctx, "tool_call", {"tool": "delete_schedule", "schedule_id": schedule_id})
        require_approval(ctx, sql=detail, tool="delete_schedule", extra={"schedule_id": schedule_id})
        deleted = await ctx.deps.repo.delete_schedule(schedule_id)
        result: dict[str, object] = {"tool": "delete_schedule", "schedule_id": schedule_id, "deleted": deleted}
        if not deleted:
            result["error"] = f"schedule {schedule_id} 不存在"
        trace_tool(ctx, "tool_result", {"tool": "delete_schedule", "result": result})
        return result

    return cap
