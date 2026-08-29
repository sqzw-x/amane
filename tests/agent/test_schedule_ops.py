"""schedule-ops Capability 表测试."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import aiosqlite
import pytest
import pytest_asyncio
from pydantic_ai import ApprovalRequired
from pydantic_ai.toolsets import FunctionToolset

from amane.agent.cache import ResultCache
from amane.agent.executor import QueryExecutor
from amane.agent.schedule_ops import AgentScheduleCreate, AgentScheduleUpdate, build_schedule_ops_capability
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.tools import AgentDeps
from amane.agent.trace import TraceEvent
from amane.api.models.tasks import CleanupSubmission, RescrapeSubmission
from amane.db.models import RoutineType
from amane.db.repository import Repository


class _MemTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)


class _Ctx:
    def __init__(
        self,
        deps: AgentDeps,
        *,
        tool_call_id: str = "tc-test",
        tool_call_approved: bool = False,
    ) -> None:
        self.deps = deps
        self.tool_call_id = tool_call_id
        self.tool_call_approved = tool_call_approved


def _toolset() -> FunctionToolset[AgentDeps]:
    toolset = build_schedule_ops_capability().get_toolset()
    assert toolset is not None
    return cast(FunctionToolset[AgentDeps], toolset)


def _tool_fn(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    return cast(Callable[..., Awaitable[dict[str, Any]]], _toolset().tools[name].function)


@pytest_asyncio.fixture
async def schedule_deps(tmp_path: Path, repo: Repository) -> AgentDeps:
    db = tmp_path / "ops.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY, title TEXT)")
        await conn.commit()
    session = await repo.create_agent_session(title="schedule-ops")
    assert session.id is not None
    return AgentDeps(
        repo=repo,
        executor=QueryExecutor(ReadonlySqlSandbox(db), ResultCache(ttl_s=60, max_entries=8)),
        session_id=session.id,
        trace=_MemTrace(),  # type: ignore[arg-type]
        sql_timeout_ms=2000,
    )


def test_schedule_ops_capability_contract() -> None:
    cap = build_schedule_ops_capability()
    assert cap.id == "schedule-ops"
    assert cap.defer_loading is True
    assert {
        "list_schedules",
        "get_schedule",
        "create_schedule",
        "update_schedule",
        "trigger_schedule",
        "delete_schedule",
    } <= set(_toolset().tools)


@pytest.mark.asyncio
async def test_create_update_and_trigger_schedule(schedule_deps: AgentDeps) -> None:
    created = await _tool_fn("create_schedule")(
        _Ctx(schedule_deps),
        request=AgentScheduleCreate(
            name="nightly", cron="0 3 * * *", submission=CleanupSubmission(type="cleanup", remove_missing_files=False)
        ),
    )
    assert created["name"] == "nightly"
    assert created["task_type"] == RoutineType.CLEANUP
    assert created["payload"] == {
        "type": "cleanup",
        "remove_missing_files": False,
        "remove_unreferenced_resources": True,
    }
    schedule_id = int(created["id"])

    updated = await _tool_fn("update_schedule")(
        _Ctx(schedule_deps), schedule_id=schedule_id, patch=AgentScheduleUpdate(cron="0 4 * * *", enabled=False)
    )
    assert updated["cron"] == "0 4 * * *"
    assert updated["enabled"] is False
    assert updated["next_run"] is not None
    assert updated["payload"] == created["payload"]

    triggered = await _tool_fn("trigger_schedule")(_Ctx(schedule_deps), schedule_id=schedule_id)
    assert triggered["next_run"] is not None
    assert triggered["task_type"] == RoutineType.CLEANUP
    assert await schedule_deps.repo.get_schedule(schedule_id) is not None


@pytest.mark.asyncio
async def test_schedule_supports_rescrape_and_rejects_invalid_changes(schedule_deps: AgentDeps) -> None:
    created = await _tool_fn("create_schedule")(
        _Ctx(schedule_deps),
        request=AgentScheduleCreate(
            cron="*/15 * * * *", submission=RescrapeSubmission(type="rescrape", limit=25, min_age_days=7)
        ),
    )
    assert created["task_type"] == RoutineType.RESCRAPE
    assert created["payload"]["limit"] == 25

    invalid_cron = await _tool_fn("update_schedule")(
        _Ctx(schedule_deps), schedule_id=int(created["id"]), patch=AgentScheduleUpdate(cron="not cron")
    )
    assert invalid_cron == {"error": "Invalid cron expression"}

    invalid_enabled = await _tool_fn("update_schedule")(
        _Ctx(schedule_deps), schedule_id=int(created["id"]), patch=AgentScheduleUpdate(enabled=None)
    )
    assert invalid_enabled == {"error": "enabled 不能为 null"}

    missing = await _tool_fn("get_schedule")(_Ctx(schedule_deps), schedule_id=9999)
    assert missing == {"error": "schedule 9999 不存在"}


@pytest.mark.asyncio
async def test_delete_schedule_requires_approval(schedule_deps: AgentDeps) -> None:
    schedule = await schedule_deps.repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={})
    assert schedule.id is not None
    with pytest.raises(ApprovalRequired):
        await _tool_fn("delete_schedule")(_Ctx(schedule_deps, tool_call_id="tc-schedule"), schedule_id=schedule.id)
    assert schedule_deps.pending["tc-schedule"].extra["schedule_id"] == schedule.id

    deleted = await _tool_fn("delete_schedule")(
        _Ctx(schedule_deps, tool_call_id="tc-schedule", tool_call_approved=True), schedule_id=schedule.id
    )
    assert deleted["deleted"] is True
