"""task-ops Capability - 统一入队 / 取消 / 重试 (不代跑)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from amane.api.models.tasks import TaskSubmission
from amane.api.support.task_resolve import resolve_submission
from amane.db.models import TaskStatus

from .tools import AgentDeps, trace_tool

_TASK_SUBMISSION_ADAPTER: TypeAdapter[TaskSubmission] = TypeAdapter(TaskSubmission)


def build_task_ops_capability() -> Capability[AgentDeps]:
    """按需加载的任务控制面."""
    cap: Capability[AgentDeps] = Capability(
        id="task-ops",
        description=(
            "Use to submit tasks (refresh/scrape/organize/…), cancel queued/running tasks, "
            "or retry failed ones. Does not execute work inline."
        ),
        instructions=(
            "submit_task body must include discriminator field type "
            "(refresh|organize|scrape|cleanup|upscale|r18_import|actor_scrape|rescrape) plus that type's fields. "
            "Prefer domain enqueue tools (metadata-ops / actor-ops / library-ops) when they fit; "
            "use task-ops for the unified submission surface or cancel/retry."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def submit_task(ctx: RunContext[AgentDeps], submission: dict[str, Any]) -> dict[str, Any]:
        """Enqueue a task from a TaskSubmission-shaped dict (type discriminator required)."""
        trace_tool(ctx, "tool_call", {"tool": "submit_task", "submission": submission})
        try:
            req = _TASK_SUBMISSION_ADAPTER.validate_python(submission)
            task_type, payload = await resolve_submission(req, ctx.deps.repo)
        except ValidationError as exc:
            return {"error": f"提交体无效: {exc.errors()[0].get('msg', str(exc))}"}
        except HTTPException as exc:
            return {"error": str(exc.detail)}
        except ValueError as exc:
            return {"error": str(exc)}
        task = await ctx.deps.repo.create_task(task_type=task_type, payload=payload)
        assert task.id is not None
        out = {"task_id": task.id, "type": task_type, "status": str(task.status)}
        trace_tool(ctx, "tool_result", {"tool": "submit_task", "result": out})
        return out

    @cap.tool
    async def cancel_task(ctx: RunContext[AgentDeps], task_id: int) -> dict[str, Any]:
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
        out = {"task_id": task_id, "cancelled": True, "previous_status": task.status}
        trace_tool(ctx, "tool_result", {"tool": "cancel_task", "result": out})
        return out

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
        out = {"original_task_id": task_id, "task_id": new_task.id, "type": str(new_task.type)}
        trace_tool(ctx, "tool_result", {"tool": "retry_task", "result": out})
        return out

    return cap
