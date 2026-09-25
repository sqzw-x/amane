"""metadata-ops Capability / explore toolset 组装表测试."""

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
from amane.agent.metadata_ops import build_metadata_ops_capability
from amane.agent.runtime import build_agent
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.tools import TOOL_OK, AgentDeps, build_explore_toolset
from amane.agent.trace import TraceEvent
from amane.config import AgentConfig
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


def _ops_toolset() -> FunctionToolset[AgentDeps]:
    toolset = build_metadata_ops_capability().get_toolset()
    assert toolset is not None
    return cast(FunctionToolset[AgentDeps], toolset)


def _tool_fn(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    return cast(Callable[..., Awaitable[dict[str, Any]]], _ops_toolset().tools[name].function)


@pytest_asyncio.fixture
async def write_deps(tmp_path: Path, repo: Repository) -> AgentDeps:
    db = tmp_path / "ops.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY, title TEXT)")
        await conn.commit()
    session = await repo.create_agent_session(title="ops")
    assert session.id is not None
    m = await repo.upsert_metadata(number="ZZZ-001", title="Orig")
    assert m.id is not None
    return AgentDeps(
        repo=repo,
        executor=QueryExecutor(ReadonlySqlSandbox(db), ResultCache(ttl_s=60, max_entries=8)),
        session_id=session.id,
        trace=_MemTrace(),  # type: ignore[arg-type]
        sql_timeout_ms=2000,
        sample_limit=5,
    )


def test_build_agent_wires_explore_and_metadata_ops() -> None:
    agent = build_agent(AgentConfig(api_key="sk-test", model="gpt-4o", base_url="https://example.com/v1"))
    assert agent is not None
    assert any(getattr(c, "id", None) == "metadata-ops" for c in agent.root_capability.capabilities)


def test_explore_toolset_has_core_tools() -> None:
    ts = build_explore_toolset()
    names = set(ts.tools.keys())
    assert {"sql_explore", "sql_deliver", "inspect_result"} <= names


def test_write_tools_not_on_explore_toolset() -> None:
    names = set(build_explore_toolset().tools.keys())
    assert "update_metadata" not in names
    assert "delete_metadata" not in names
    assert "enqueue_scrape" not in names


def test_metadata_ops_capability_tools() -> None:
    names = set(_ops_toolset().tools.keys())
    assert "update_metadata" in names
    assert "delete_metadata" in names
    assert "enqueue_scrape" in names
    assert "batch_user_tags" in names


@pytest.mark.asyncio
async def test_update_metadata_tool(write_deps: AgentDeps) -> None:
    items, _total = await write_deps.repo.list_metadata(limit=10)
    mid = items[0].id
    assert mid is not None
    out = await _tool_fn("update_metadata")(_Ctx(write_deps), metadata_id=mid, patch={"title": "Patched"})
    assert out == TOOL_OK
    row = await write_deps.repo.get_metadata(mid)
    assert row is not None
    assert row.title == "Patched"


@pytest.mark.asyncio
async def test_update_metadata_rejects_unknown_field_with_writable_list(write_deps: AgentDeps) -> None:
    """拒绝未知字段时须一并回可写字段, 否则模型只能反复试探."""
    items, _total = await write_deps.repo.list_metadata(limit=10)
    mid = items[0].id
    assert mid is not None
    out = await _tool_fn("update_metadata")(_Ctx(write_deps), metadata_id=mid, patch={"nope": 1})
    assert out["error"].startswith("不允许的字段: nope; 可写字段: ")
    assert "title" in out["error"]


@pytest.mark.asyncio
async def test_delete_metadata_registers_approval(write_deps: AgentDeps) -> None:
    with pytest.raises(ApprovalRequired):
        await _tool_fn("delete_metadata")(_Ctx(write_deps, tool_call_id="tc-del-md"), metadata_id=1)
    assert len(write_deps.pending) == 1
    pending = write_deps.pending["tc-del-md"]
    assert pending.tool == "delete_metadata"
    assert pending.extra.get("metadata_id") == 1
    result = await _tool_fn("delete_metadata")(
        _Ctx(write_deps, tool_call_id="tc-del-md", tool_call_approved=True), metadata_id=1
    )
    assert result == TOOL_OK
    assert await write_deps.repo.get_metadata(1) is None


@pytest.mark.asyncio
async def test_user_tag_tool_reports_counts_and_names_unknown_tag(write_deps: AgentDeps) -> None:
    """请求级失败须点名出错输入; 已是目标态是幂等命中, 不是第三种失败成因."""
    items, _total = await write_deps.repo.list_metadata(limit=1)
    metadata_id = items[0].id
    assert metadata_id is not None
    tags, _created = await write_deps.repo.ensure_user_tags(["标签"])
    tag = tags[0]
    assert tag.id is not None

    tool = _tool_fn("batch_user_tags")
    assert await tool(_Ctx(write_deps), metadata_ids=[metadata_id], user_tag_ids=[9999]) == {
        "error": "用户标签不存在: 9999"
    }
    assert await tool(_Ctx(write_deps), metadata_ids=[], user_tag_ids=[tag.id]) == {"error": "metadata_ids 为空"}
    assert await tool(_Ctx(write_deps), metadata_ids=[metadata_id], user_tag_ids=[]) == {"error": "user_tag_ids 为空"}

    assert await tool(_Ctx(write_deps), metadata_ids=[metadata_id], user_tag_ids=[tag.id]) == {
        "changed": 1,
        "unchanged": 0,
        "missing": 0,
    }
    # 已挂载是幂等成功, 不是第三种失败成因
    assert await tool(_Ctx(write_deps), metadata_ids=[metadata_id], user_tag_ids=[tag.id]) == {
        "changed": 0,
        "unchanged": 1,
        "missing": 0,
    }
    assert await tool(_Ctx(write_deps), metadata_ids=[metadata_id, 9999], user_tag_ids=[tag.id], action="detach") == {
        "changed": 1,
        "unchanged": 0,
        "missing": 1,
    }
