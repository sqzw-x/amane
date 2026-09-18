"""不代为执行; 只入队 / 取消 / 重试."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.support.task_resolve import resolve_submission
from ..db.models import TaskStatus
from .payload_schema import TASK_SUBMISSION, TaskSubmissionType
from .tools import TOOL_OK, AgentDeps, trace_tool


def build_task_ops_capability() -> Capability[AgentDeps]:
    cap: Capability[AgentDeps] = Capability(
        id="task-ops",
        instructions=(
            "Prefer the domain enqueue tools (enqueue_scrape / enqueue_actor_scrape / "
            "enqueue_library_refresh) when one covers the request; submit_task is the unified "
            "surface for the rest and does not execute work inline."
        ),
    )

    @cap.tool
    async def get_task_submission_schema(
        ctx: RunContext[AgentDeps], submission_type: TaskSubmissionType
    ) -> dict[str, Any]:
        """Return the accepted fields for one task submission type."""
        trace_tool(ctx, "tool_call", {"tool": "get_task_submission_schema", "type": submission_type})
        out = TASK_SUBMISSION.schema(submission_type)
        trace_tool(ctx, "tool_result", {"tool": "get_task_submission_schema", "type": submission_type})
        return out

    @cap.tool
    async def submit_task(ctx: RunContext[AgentDeps], submission: dict[str, Any]) -> dict[str, Any]:
        """Enqueue a task; the body shape comes from get_task_submission_schema."""
        trace_tool(ctx, "tool_call", {"tool": "submit_task", "submission": submission})
        try:
            req = TASK_SUBMISSION.validate(submission)
            task_type, payload = await resolve_submission(req, ctx.deps.repo)
        except ValidationError as exc:
            return TASK_SUBMISSION.error(submission, exc)
        except HTTPException as exc:
            return {"error": str(exc.detail)}
        except ValueError as exc:
            return {"error": str(exc)}
        task = await ctx.deps.repo.create_task(task_type=task_type, payload=payload)
        assert task.id is not None
        out = {"task_id": task.id}
        trace_tool(ctx, "tool_result", {"tool": "submit_task", "result": out})
        return out

    @cap.tool
    async def cancel_task(ctx: RunContext[AgentDeps], task_id: int) -> str | dict[str, Any]:
        """Cancel a queued or running task."""
        trace_tool(ctx, "tool_call", {"tool": "cancel_task", "task_id": task_id})
        task = await ctx.deps.repo.get_task(task_id)
        if task is None:
            return {"error": f"task {task_id} 不存在"}
        if task.status == TaskStatus.RUNNING:
            cancel_fn = ctx.deps.bridge.cancel_running_task
            if cancel_fn is None:
                await ctx.deps.repo.fail_task(task_id, error="Cancelled by user")
            else:
                cancelled = await cancel_fn(task_id)
                if not cancelled:
                    await ctx.deps.repo.fail_task(task_id, error="Cancelled by user")
        elif task.status == TaskStatus.QUEUED:
            await ctx.deps.repo.fail_task(task_id, error="Cancelled by user")
        else:
            return {"error": f"无法取消状态为 '{task.status}' 的任务"}
        trace_tool(ctx, "tool_result", {"tool": "cancel_task", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def retry_task(ctx: RunContext[AgentDeps], task_id: int) -> dict[str, Any]:
        """Retry a failed task by enqueueing a new one with the same type/payload."""
        trace_tool(ctx, "tool_call", {"tool": "retry_task", "task_id": task_id})
        task = await ctx.deps.repo.get_task(task_id)
        if task is None:
            return {"error": f"task {task_id} 不存在"}
        if task.status != TaskStatus.FAILED:
            return {"error": "仅失败任务可重试"}
        new_task = await ctx.deps.repo.create_task(task_type=task.type, payload=task.payload, priority=task.priority)
        assert new_task.id is not None
        out = {"task_id": new_task.id}
        trace_tool(ctx, "tool_result", {"tool": "retry_task", "result": out})
        return out

    return cap
