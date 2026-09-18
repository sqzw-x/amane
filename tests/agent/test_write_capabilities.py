"""写操作 Capability 表测试: actor / facet / library / task."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import aiosqlite
import httpx2
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
from amane.agent.metadata_ops import build_metadata_ops_capability
from amane.agent.runtime import build_agent
from amane.agent.schedule_ops import build_schedule_ops_capability
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.task_ops import build_task_ops_capability
from amane.agent.tools import TOOL_OK, AgentDeps
from amane.agent.trace import TraceEvent
from amane.config import AgentConfig
from amane.db.models import FacetKind, TaskStatus, TaskType
from amane.db.repository import Repository
from amane.enums import ApiType
from amane.handlers.models import ScrapePayload
from amane.llm import build_model


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
    assert out == TOOL_OK
    actor = await write_deps.repo.get_actor(actor_id)
    assert actor is not None
    assert actor.overview == "bio"
    scrape = await _tool_fn(build_actor_ops_capability(), "enqueue_actor_scrape")(
        _Ctx(write_deps), actor_ids=[actor_id]
    )
    assert scrape == {"submitted": 1, "missing": 0}
    tasks = await write_deps.repo.list_tasks()
    assert [task.type for task in tasks] == [TaskType.ACTOR_SCRAPE]


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
    missing = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="Nobody")
    assert missing["matches"] == []

    added = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert added == TOOL_OK
    assert await write_deps.repo.get_actor_aliases(actor_id) == ["旧名"]
    dup = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert "已存在" in dup["error"]
    resolved2 = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="旧名")
    assert resolved2["matches"] == [{"id": actor_id, "name": "Alice", "is_display": False}]

    removed = await _tool_fn(cap, "remove_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert removed == TOOL_OK
    assert await write_deps.repo.get_actor_aliases(actor_id) == []
    gone = await _tool_fn(cap, "remove_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="旧名")
    assert "不存在" in gone["error"]
    self_alias = await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="Alice")
    assert "展示名" in self_alias["error"]

    await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=actor_id, name="Preferred")
    switched = await _tool_fn(cap, "set_actor_display_name")(_Ctx(write_deps), actor_id=actor_id, name="Preferred")
    assert switched == TOOL_OK
    listed2 = await _tool_fn(cap, "get_actor_aliases")(_Ctx(write_deps), actor_id=actor_id)
    assert listed2["name"] == "Preferred"
    assert "Alice" in listed2["aliases"]


@pytest.mark.asyncio
async def test_resolve_shared_alias_reports_ambiguous(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="SH-001", title="t", actors=["One", "Two"])
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    cap = build_actor_ops_capability()
    for item in items:
        await _tool_fn(cap, "add_actor_alias")(_Ctx(write_deps), actor_id=item.id, name="共享")
    resolved = await _tool_fn(cap, "resolve_actor_name")(_Ctx(write_deps), name="共享")
    assert len(resolved["matches"]) == 2


@pytest.mark.asyncio
async def test_rename_facet_and_delete_needs_approval(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="F-001", title="t", studio="StudioA")
    items, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    facet_id = items[0].id
    renamed = await _tool_fn(build_facet_identity_capability(), "rename_facet")(
        _Ctx(write_deps), kind=FacetKind.STUDIO, facet_id=facet_id, name="StudioB"
    )
    assert renamed == TOOL_OK
    studios, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    assert [item.name for item in studios] == ["StudioB"]
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
    assert deleted == TOOL_OK
    studios, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    assert studios == []


@pytest.mark.asyncio
async def test_library_create_refresh_and_delete_approval(write_deps: AgentDeps, tmp_path: Path) -> None:
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    created = await _tool_fn(build_library_ops_capability(), "create_library")(
        _Ctx(write_deps), path=str(lib_dir), name="L1", scan=True
    )
    library_id = int(created["library_id"])
    assert created.get("refresh_task_id") is not None
    outside = await _tool_fn(build_library_ops_capability(), "create_library")(
        _Ctx(write_deps), path="/etc", scan=False
    )
    assert "error" in outside
    with pytest.raises(ApprovalRequired):
        await _tool_fn(build_library_ops_capability(), "delete_library")(
            _Ctx(write_deps, tool_call_id="tc-del-lib"), library_id=library_id
        )
    assert write_deps.pending["tc-del-lib"].tool == "delete_library"
    deleted = await _tool_fn(build_library_ops_capability(), "delete_library")(
        _Ctx(write_deps, tool_call_id="tc-del-lib", tool_call_approved=True), library_id=library_id
    )
    assert deleted == TOOL_OK
    assert await write_deps.repo.get_library(library_id) is None


@pytest.mark.asyncio
async def test_task_submit_cancel_retry(write_deps: AgentDeps) -> None:
    submitted = await _tool_fn(build_task_ops_capability(), "submit_task")(
        _Ctx(write_deps), submission={"type": "scrape", "number": "TSK-001"}
    )
    task_id = int(submitted["task_id"])
    cancelled = await _tool_fn(build_task_ops_capability(), "cancel_task")(_Ctx(write_deps), task_id=task_id)
    assert cancelled == TOOL_OK
    task = await write_deps.repo.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    # force failed with same payload for retry path when already failed
    retried = await _tool_fn(build_task_ops_capability(), "retry_task")(_Ctx(write_deps), task_id=task_id)
    new_task_id = int(retried["task_id"])
    assert new_task_id != task_id
    new_task = await write_deps.repo.get_task(new_task_id)
    assert new_task is not None
    assert new_task.type == TaskType.SCRAPE
    assert new_task.payload == task.payload


@pytest.mark.asyncio
async def test_task_submit_invalid_body(write_deps: AgentDeps) -> None:
    out = await _tool_fn(build_task_ops_capability(), "submit_task")(_Ctx(write_deps), submission={"type": "scrape"})
    # 失败时返回出错字段, 可用类型, 以及该类型的字段定义
    assert out["error"].startswith("参数无效: scrape:")
    assert "rescrape" in out["types"]
    assert out["schema"]["properties"]["type"]["const"] == "scrape"


@pytest.mark.asyncio
async def test_task_retry_rejects_non_failed(write_deps: AgentDeps) -> None:
    task = await write_deps.repo.create_task(task_type=TaskType.SCRAPE, payload=ScrapePayload(number="X-1"))
    assert task.id is not None
    out = await _tool_fn(build_task_ops_capability(), "retry_task")(_Ctx(write_deps), task_id=task.id)
    assert "error" in out


def _tool_names(payload: dict[str, Any]) -> list[str]:
    """Responses 用 ``name``, Chat Completions 嵌在 ``function`` 里."""
    return [tool.get("name") or tool["function"]["name"] for tool in payload.get("tools") or []]


_RESPONSES_USAGE = {
    "input_tokens": 1,
    "output_tokens": 1,
    "total_tokens": 2,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 0},
}
_DONE: dict[ApiType, dict[str, Any]] = {
    ApiType.RESPONSE: {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "deepseek-v4-flash",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "好", "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": _RESPONSES_USAGE,
    },
    ApiType.CHAT: {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "好"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    },
}


def _write_tool_names() -> set[str]:
    caps = [
        build_metadata_ops_capability(),
        build_actor_ops_capability(),
        build_facet_identity_capability(),
        build_library_ops_capability(),
        build_feed_ops_capability(),
        build_schedule_ops_capability(),
        build_task_ops_capability(),
    ]
    return {name for cap in caps for name in _cap_toolset(cap).tools}


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", [ApiType.CHAT, ApiType.RESPONSE])
async def test_write_tools_declared_up_front(write_deps: AgentDeps, api_type: ApiType) -> None:
    """写操作工具必须在第一个请求就全部声明, 且不存在 ``load_capability``.

    兼容端点没有"声明了但不开放"的通道 (见 docs/dev/agent.md), 一旦某域重新延迟载入, 工具就会
    只在载入后的请求里出现 —— 这条断言会失败.
    """
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return httpx2.Response(200, json=_DONE[api_type])

    agent = build_agent(AgentConfig(api_key="sk-test", model="deepseek-flash", base_url="https://api.deepseek.com"))
    assert agent is not None
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(
            api_type,
            base_url="https://api.deepseek.com",
            api_key="sk-test",
            model="deepseek-flash",
            http_client=client,
        )
        result = await agent.run("列出来源", deps=write_deps, model=model)

    assert result.output == "好"
    names = set(_tool_names(payloads[0]))
    assert "load_capability" not in names
    assert _write_tool_names() <= names
    assert {"sql_explore", "sql_deliver", "inspect_result"} <= names
