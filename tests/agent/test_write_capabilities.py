"""写面 Capability 表测试: actor / facet / library / task."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import aiosqlite
import pytest
import pytest_asyncio
from pydantic_ai import ApprovalRequired
from pydantic_ai.toolsets import FunctionToolset

from amane.agent.actor_ops import build_actor_ops_capability
from amane.agent.bridge import AgentRuntimeBridge
from amane.agent.cache import ResultCache
from amane.agent.executor import QueryExecutor
from amane.agent.facet_identity import build_facet_identity_capability
from amane.agent.feed_ops import build_feed_ops_capability
from amane.agent.library_ops import build_library_ops_capability
from amane.agent.runtime import build_agent
from amane.agent.schedule_ops import build_schedule_ops_capability
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.task_ops import build_task_ops_capability
from amane.agent.tools import AgentDeps
from amane.agent.trace import TraceEvent
from amane.config import AgentConfig
from amane.db.models import FacetKind, TaskStatus, TaskType
from amane.db.repository import Repository
from amane.handlers.models import ScrapePayload


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


def _cap_toolset(cap: Any) -> FunctionToolset[AgentDeps]:
    toolset = cap.get_toolset()
    assert toolset is not None
    return cast(FunctionToolset[AgentDeps], toolset)


def _tool_fn(cap: Any, name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    return cast(Callable[..., Awaitable[dict[str, Any]]], _cap_toolset(cap).tools[name].function)


@pytest_asyncio.fixture
async def write_deps(tmp_path: Path, repo: Repository) -> AgentDeps:
    db = tmp_path / "ops.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY, title TEXT)")
        await conn.commit()
    session = await repo.create_agent_session(title="ops")
    assert session.id is not None
    return AgentDeps(
        repo=repo,
        executor=QueryExecutor(ReadonlySqlSandbox(db), ResultCache(ttl_s=60, max_entries=8)),
        session_id=session.id,
        trace=_MemTrace(),  # type: ignore[arg-type]
        sql_timeout_ms=2000,
        sample_limit=5,
        bridge=AgentRuntimeBridge(safe_dirs=[tmp_path.resolve()]),
    )


@pytest.mark.parametrize(
    ("cap_id", "builder"),
    [
        ("actor-ops", build_actor_ops_capability),
        ("facet-identity", build_facet_identity_capability),
        ("library-ops", build_library_ops_capability),
        ("feed-ops", build_feed_ops_capability),
        ("schedule-ops", build_schedule_ops_capability),
        ("task-ops", build_task_ops_capability),
    ],
)
def test_write_capabilities_deferred(cap_id: str, builder: Callable[[], Any]) -> None:
    cap = builder()
    assert cap.id == cap_id
    assert cap.defer_loading is True


def test_build_agent_wires_all_write_capabilities() -> None:
    agent = build_agent(AgentConfig(api_key="sk-test", model="gpt-4o", base_url="https://example.com/v1"))
    assert agent is not None
    ids = {getattr(c, "id", None) for c in agent.root_capability.capabilities}
    assert {
        "metadata-ops",
        "actor-ops",
        "facet-identity",
        "library-ops",
        "feed-ops",
        "schedule-ops",
        "task-ops",
    } <= ids


@pytest.mark.asyncio
async def test_update_actor_and_enqueue_scrape(write_deps: AgentDeps) -> None:
    m = await write_deps.repo.upsert_metadata(number="ACT-001", title="t", actors=["Alice"])
    assert m.id is not None
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    assert items
    actor_id = items[0].id
    out = await _tool_fn(build_actor_ops_capability(), "update_actor")(
        _Ctx(write_deps), actor_id=actor_id, patch={"overview": "bio"}
    )
    assert out.get("updated") is True
    scrape = await _tool_fn(build_actor_ops_capability(), "enqueue_actor_scrape")(
        _Ctx(write_deps), actor_ids=[actor_id]
    )
    assert scrape.get("submitted") == 1
    assert scrape.get("task_ids")


@pytest.mark.asyncio
async def test_actor_alias_tools(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="ALIAS-001", title="t", actors=["Alice"])
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    actor_id = items[0].id
    cap = build_actor_ops_capability()

    listed = await _tool_fn(cap, "get_actor_aliases")(_Ctx(write_deps), actor_id=actor_id)
    assert listed["name"] == "Alice"
    assert listed["aliases"] == []

    resolved = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="Alice")
    assert resolved["matches"] == [{"id": actor_id, "name": "Alice", "is_display": True}]
    assert resolved["ambiguous"] is False
    missing = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="Nobody")
    assert missing["matches"] == []

    added = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert added.get("added") is True
    dup = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert "已存在" in dup["error"]
    resolved2 = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="旧名")
    assert resolved2["matches"] == [{"id": actor_id, "name": "Alice", "is_display": False}]
    assert resolved2["ambiguous"] is False

    removed = await _tool_fn(cap, "remove_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert removed.get("removed") is True
    gone = await _tool_fn(cap, "remove_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert "不存在" in gone["error"]
    self_alias = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="Alice")
    assert "展示名" in self_alias["error"]

    await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="Preferred")
    switched = await _tool_fn(cap, "set_actor_display_name")(_Ctx(write_deps), actor_id=actor_id, name="Preferred")
    assert switched.get("name") == "Preferred"
    listed2 = await _tool_fn(cap, "get_actor_aliases")(_Ctx(write_deps), actor_id=actor_id)
    assert "Alice" in listed2["aliases"]


@pytest.mark.asyncio
async def test_resolve_shared_alias_reports_ambiguous(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="SH-001", title="t", actors=["One", "Two"])
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    cap = build_actor_ops_capability()
    for item in items:
        await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=item.id, name="共享")
    resolved = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="共享")
    assert resolved["ambiguous"] is True
    assert len(resolved["matches"]) == 2


@pytest.mark.asyncio
async def test_rename_facet_and_delete_needs_approval(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="F-001", title="t", studio="StudioA")
    items, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    facet_id = items[0].id
    renamed = await _tool_fn(build_facet_identity_capability(), "rename_facet")(
        _Ctx(write_deps), kind=FacetKind.STUDIO, facet_id=facet_id, name="StudioB"
    )
    assert renamed.get("name") == "StudioB"
    with pytest.raises(ApprovalRequired):
        await _tool_fn(build_facet_identity_capability(), "delete_facet")(
            _Ctx(write_deps, tool_call_id="tc-del-facet"), kind=FacetKind.STUDIO, facet_id=facet_id
        )
    pending = write_deps.pending["tc-del-facet"]
    assert pending.tool == "delete_facet"
    assert pending.extra.get("facet_id") == facet_id
    deleted = await _tool_fn(build_facet_identity_capability(), "delete_facet")(
        _Ctx(write_deps, tool_call_id="tc-del-facet", tool_call_approved=True), kind=FacetKind.STUDIO, facet_id=facet_id
    )
    assert deleted.get("deleted") is True


@pytest.mark.asyncio
async def test_library_create_refresh_and_delete_approval(write_deps: AgentDeps, tmp_path: Path) -> None:
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    created = await _tool_fn(build_library_ops_capability(), "create_library")(
        _Ctx(write_deps), path=str(lib_dir), name="L1", scan=True
    )
    assert created.get("id") is not None
    assert created.get("refresh_task_id") is not None
    outside = await _tool_fn(build_library_ops_capability(), "create_library")(
        _Ctx(write_deps), path="/etc", scan=False
    )
    assert "error" in outside
    with pytest.raises(ApprovalRequired):
        await _tool_fn(build_library_ops_capability(), "delete_library")(
            _Ctx(write_deps, tool_call_id="tc-del-lib"), library_id=int(created["id"])
        )
    assert write_deps.pending["tc-del-lib"].tool == "delete_library"
    deleted = await _tool_fn(build_library_ops_capability(), "delete_library")(
        _Ctx(write_deps, tool_call_id="tc-del-lib", tool_call_approved=True), library_id=int(created["id"])
    )
    assert deleted.get("deleted") is True


@pytest.mark.asyncio
async def test_task_submit_cancel_retry(write_deps: AgentDeps) -> None:
    submitted = await _tool_fn(build_task_ops_capability(), "submit_task")(
        _Ctx(write_deps), submission={"type": "scrape", "number": "TSK-001"}
    )
    assert submitted.get("task_id") is not None
    task_id = int(submitted["task_id"])
    cancelled = await _tool_fn(build_task_ops_capability(), "cancel_task")(_Ctx(write_deps), task_id=task_id)
    assert cancelled.get("cancelled") is True
    task = await write_deps.repo.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    # force failed with same payload for retry path when already failed
    retried = await _tool_fn(build_task_ops_capability(), "retry_task")(_Ctx(write_deps), task_id=task_id)
    assert retried.get("task_id") is not None
    assert retried.get("original_task_id") == task_id


@pytest.mark.asyncio
async def test_task_submit_invalid_body(write_deps: AgentDeps) -> None:
    out = await _tool_fn(build_task_ops_capability(), "submit_task")(_Ctx(write_deps), submission={"type": "scrape"})
    assert "error" in out


@pytest.mark.asyncio
async def test_task_retry_rejects_non_failed(write_deps: AgentDeps) -> None:
    task = await write_deps.repo.create_task(task_type=TaskType.SCRAPE, payload=ScrapePayload(number="X-1"))
    assert task.id is not None
    out = await _tool_fn(build_task_ops_capability(), "retry_task")(_Ctx(write_deps), task_id=task.id)
    assert "error" in out
