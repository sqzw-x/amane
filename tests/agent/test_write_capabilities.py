"""写操作 Capability 表测试: actor / facet / library / task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import httpx2
import pytest
import pytest_asyncio
from pydantic_ai import ApprovalRequired

from amane.agent.actor_ops import build_actor_ops_capability
from amane.agent.bridge import AgentRuntimeBridge
from amane.agent.cache import CachedResult, ResultCache
from amane.agent.capabilities import build_capabilities
from amane.agent.comment_ops import build_comment_ops_capability
from amane.agent.executor import QueryExecutor
from amane.agent.facet_identity import build_facet_identity_capability
from amane.agent.library_ops import build_library_ops_capability
from amane.agent.metadata_ops import build_metadata_ops_capability
from amane.agent.runtime import build_agent
from amane.agent.saved_query_ops import build_saved_query_ops_capability
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.task_ops import build_task_ops_capability
from amane.agent.tools import TOOL_OK, AgentDeps
from amane.api.models.comments import COMMENT_BODY_MAX
from amane.api.models.saved_queries import (
    SAVED_QUERY_DESCRIPTION_MAX,
    SAVED_QUERY_IDS_MAX,
    SAVED_QUERY_NAME_MAX,
)
from amane.config import AgentConfig
from amane.db.models import FacetKind, SavedQueryEntity, TaskStatus, TaskType
from amane.db.repository import Repository
from amane.enums import ApiType
from amane.handlers.models import ScrapePayload
from amane.llm import build_model
from tests.agent.support import ToolCallContext, cap_toolset, tool_fn


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
        "comment-ops",
        "saved-query-ops",
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
    out = await tool_fn(build_actor_ops_capability(), "update_actor")(
        ToolCallContext(write_deps), actor_id=actor_id, patch={"overview": "bio"}
    )
    assert out == TOOL_OK
    actor = await write_deps.repo.get_actor(actor_id)
    assert actor is not None
    assert actor.overview == "bio"
    # 助理写入属 MANUAL: 所改字段自动上锁.
    assert actor.locked_fields == ["overview"]
    scrape = await tool_fn(build_actor_ops_capability(), "enqueue_actor_scrape")(
        ToolCallContext(write_deps), actor_ids=[actor_id]
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

    listed = await tool_fn(cap, "get_actor_aliases")(ToolCallContext(write_deps), actor_id=actor_id)
    assert listed["name"] == "Alice"
    assert listed["aliases"] == []

    resolved = await tool_fn(cap, "resolve_actor_name")(ToolCallContext(write_deps), name="Alice")
    assert resolved["matches"] == [{"id": actor_id, "name": "Alice", "is_display": True}]
    missing = await tool_fn(cap, "resolve_actor_name")(ToolCallContext(write_deps), name="Nobody")
    assert missing["matches"] == []

    added = await tool_fn(cap, "add_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="旧名")
    assert added == TOOL_OK
    assert await write_deps.repo.get_actor_aliases(actor_id) == ["旧名"]
    dup = await tool_fn(cap, "add_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="旧名")
    assert "已存在" in dup["error"]
    resolved2 = await tool_fn(cap, "resolve_actor_name")(ToolCallContext(write_deps), name="旧名")
    assert resolved2["matches"] == [{"id": actor_id, "name": "Alice", "is_display": False}]

    removed = await tool_fn(cap, "remove_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="旧名")
    assert removed == TOOL_OK
    assert await write_deps.repo.get_actor_aliases(actor_id) == []
    gone = await tool_fn(cap, "remove_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="旧名")
    assert "不存在" in gone["error"]
    self_alias = await tool_fn(cap, "add_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="Alice")
    assert "展示名" in self_alias["error"]

    await tool_fn(cap, "add_actor_alias")(ToolCallContext(write_deps), actor_id=actor_id, name="Preferred")
    switched = await tool_fn(cap, "set_actor_display_name")(
        ToolCallContext(write_deps), actor_id=actor_id, name="Preferred"
    )
    assert switched == TOOL_OK
    listed2 = await tool_fn(cap, "get_actor_aliases")(ToolCallContext(write_deps), actor_id=actor_id)
    assert listed2["name"] == "Preferred"
    assert "Alice" in listed2["aliases"]


@pytest.mark.asyncio
async def test_resolve_shared_alias_reports_ambiguous(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="SH-001", title="t", actors=["One", "Two"])
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    cap = build_actor_ops_capability()
    for item in items:
        await tool_fn(cap, "add_actor_alias")(ToolCallContext(write_deps), actor_id=item.id, name="共享")
    resolved = await tool_fn(cap, "resolve_actor_name")(ToolCallContext(write_deps), name="共享")
    assert len(resolved["matches"]) == 2


@pytest.mark.asyncio
async def test_rename_facet_and_delete_needs_approval(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="F-001", title="t", studio="StudioA")
    items, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    facet_id = items[0].id
    renamed = await tool_fn(build_facet_identity_capability(), "rename_facet")(
        ToolCallContext(write_deps), kind=FacetKind.STUDIO, facet_id=facet_id, name="StudioB"
    )
    assert renamed == TOOL_OK
    studios, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    assert [item.name for item in studios] == ["StudioB"]
    with pytest.raises(ApprovalRequired) as exc:
        await tool_fn(build_facet_identity_capability(), "delete_facet")(
            ToolCallContext(write_deps, tool_call_id="tc-del-facet"), kind=FacetKind.STUDIO, facet_id=facet_id
        )
    meta = exc.value.metadata or {}
    assert meta["tool"] == "delete_facet"
    assert meta["extra"].get("facet_id") == facet_id
    deleted = await tool_fn(build_facet_identity_capability(), "delete_facet")(
        ToolCallContext(write_deps, tool_call_id="tc-del-facet", tool_call_approved=True),
        kind=FacetKind.STUDIO,
        facet_id=facet_id,
    )
    assert deleted == TOOL_OK
    studios, _ = await write_deps.repo.list_facets(FacetKind.STUDIO, limit=10)
    assert studios == []


@pytest.mark.asyncio
async def test_library_create_refresh_and_delete_approval(write_deps: AgentDeps, tmp_path: Path) -> None:
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    created = await tool_fn(build_library_ops_capability(), "create_library")(
        ToolCallContext(write_deps), path=str(lib_dir), name="L1", scan=True
    )
    library_id = int(created["library_id"])
    assert created.get("refresh_task_id") is not None
    outside = await tool_fn(build_library_ops_capability(), "create_library")(
        ToolCallContext(write_deps), path="/etc", scan=False
    )
    assert "error" in outside
    with pytest.raises(ApprovalRequired) as exc:
        await tool_fn(build_library_ops_capability(), "delete_library")(
            ToolCallContext(write_deps, tool_call_id="tc-del-lib"), library_id=library_id
        )
    assert (exc.value.metadata or {})["tool"] == "delete_library"
    deleted = await tool_fn(build_library_ops_capability(), "delete_library")(
        ToolCallContext(write_deps, tool_call_id="tc-del-lib", tool_call_approved=True), library_id=library_id
    )
    assert deleted == TOOL_OK
    assert await write_deps.repo.get_library(library_id) is None


@pytest.mark.asyncio
async def test_task_submit_cancel_retry(write_deps: AgentDeps) -> None:
    submitted = await tool_fn(build_task_ops_capability(), "submit_task")(
        ToolCallContext(write_deps), submission={"type": "scrape", "number": "TSK-001"}
    )
    task_id = int(submitted["task_id"])
    cancelled = await tool_fn(build_task_ops_capability(), "cancel_task")(ToolCallContext(write_deps), task_id=task_id)
    assert cancelled == TOOL_OK
    task = await write_deps.repo.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    # force failed with same payload for retry path when already failed
    retried = await tool_fn(build_task_ops_capability(), "retry_task")(ToolCallContext(write_deps), task_id=task_id)
    new_task_id = int(retried["task_id"])
    assert new_task_id != task_id
    new_task = await write_deps.repo.get_task(new_task_id)
    assert new_task is not None
    assert new_task.type == TaskType.SCRAPE
    assert new_task.payload == task.payload


@pytest.mark.asyncio
async def test_task_submit_invalid_body(write_deps: AgentDeps) -> None:
    out = await tool_fn(build_task_ops_capability(), "submit_task")(
        ToolCallContext(write_deps), submission={"type": "scrape"}
    )
    # 失败时返回出错字段, 可用类型, 以及该类型的字段定义
    assert out["error"].startswith("参数无效: scrape:")
    assert "rescrape" in out["types"]
    assert out["schema"]["properties"]["type"]["const"] == "scrape"


@pytest.mark.asyncio
async def test_task_retry_rejects_non_failed(write_deps: AgentDeps) -> None:
    task = await write_deps.repo.create_task(task_type=TaskType.SCRAPE, payload=ScrapePayload(number="X-1"))
    assert task.id is not None
    out = await tool_fn(build_task_ops_capability(), "retry_task")(ToolCallContext(write_deps), task_id=task.id)
    assert "error" in out


@pytest.mark.asyncio
async def test_comment_tools(write_deps: AgentDeps) -> None:
    meta = await write_deps.repo.upsert_metadata(number="CMT-001", title="t")
    assert meta.id is not None
    cap = build_comment_ops_capability()

    created = await tool_fn(cap, "create_comment")(ToolCallContext(write_deps), metadata_id=meta.id, body="  第一条  ")
    comment_id = int(created["comment_id"])
    assert [c.body for c in await write_deps.repo.list_comments(meta.id)] == ["第一条"]

    updated = await tool_fn(cap, "update_comment")(ToolCallContext(write_deps), comment_id=comment_id, body="改过")
    assert updated == TOOL_OK
    rows = await write_deps.repo.list_comments(meta.id)
    assert rows[0].body == "改过"
    assert rows[0].updated_at > rows[0].created_at

    with pytest.raises(ApprovalRequired) as exc:
        await tool_fn(cap, "delete_comment")(ToolCallContext(write_deps, tool_call_id="tc-cmt"), comment_id=comment_id)
    assert (exc.value.metadata or {})["tool"] == "delete_comment"
    deleted = await tool_fn(cap, "delete_comment")(
        ToolCallContext(write_deps, tool_call_id="tc-cmt", tool_call_approved=True), comment_id=comment_id
    )
    assert deleted == TOOL_OK
    assert await write_deps.repo.list_comments(meta.id) == []


@pytest.mark.asyncio
async def test_comment_tool_rejects_bad_body(write_deps: AgentDeps) -> None:
    cap = build_comment_ops_capability()
    for body in ("   ", "x" * (COMMENT_BODY_MAX + 1)):
        out = await tool_fn(cap, "create_comment")(ToolCallContext(write_deps), metadata_id=1, body=body)
        assert "error" in out

    missing = await tool_fn(cap, "create_comment")(ToolCallContext(write_deps), metadata_id=999999, body="ok")
    assert missing == {"error": "metadata 999999 不存在"}
    absent = await tool_fn(cap, "update_comment")(ToolCallContext(write_deps), comment_id=999999, body="ok")
    assert "error" in absent


@pytest.mark.asyncio
async def test_ensure_user_tags_and_attach(write_deps: AgentDeps) -> None:
    meta = await write_deps.repo.upsert_metadata(number="TAG-001", title="t")
    assert meta.id is not None
    facet_cap = build_facet_identity_capability()

    out = await tool_fn(facet_cap, "ensure_user_tags")(ToolCallContext(write_deps), names=[" 精选 ", "精选", "新标签"])
    assert out["created"] == 2
    assert [tag["name"] for tag in out["tags"]] == ["精选", "新标签"]
    ids = [tag["id"] for tag in out["tags"]]

    again = await tool_fn(facet_cap, "ensure_user_tags")(ToolCallContext(write_deps), names=["精选"])
    assert again["created"] == 0
    assert again["tags"][0]["id"] == ids[0]

    attached = await tool_fn(build_metadata_ops_capability(), "batch_user_tags")(
        ToolCallContext(write_deps), metadata_ids=[meta.id], user_tag_ids=ids
    )
    assert attached == {"changed": 1, "unchanged": 0, "missing": 0}
    assert sorted(tag.name for tag in await write_deps.repo.list_metadata_user_tags(meta.id)) == ["新标签", "精选"]

    blank = await tool_fn(facet_cap, "ensure_user_tags")(ToolCallContext(write_deps), names=["  ", ""])
    assert "error" in blank


@pytest.mark.asyncio
async def test_actor_user_tags_and_lock_release(write_deps: AgentDeps) -> None:
    await write_deps.repo.upsert_metadata(number="ACT-002", title="t", actors=["Bob"])
    items, _ = await write_deps.repo.list_facets(FacetKind.ACTOR, limit=10)
    actor_id = items[0].id
    assert actor_id is not None

    tags = await tool_fn(build_facet_identity_capability(), "ensure_user_tags")(
        ToolCallContext(write_deps), names=["待补"]
    )
    tag_id = int(tags["tags"][0]["id"])
    attached = await tool_fn(build_actor_ops_capability(), "batch_actor_user_tags")(
        ToolCallContext(write_deps), actor_ids=[actor_id], user_tag_ids=[tag_id]
    )
    assert attached == {"changed": 1, "unchanged": 0, "missing": 0}
    assert [tag.name for tag in await write_deps.repo.list_actor_user_tags(actor_id)] == ["待补"]
    unknown = await tool_fn(build_actor_ops_capability(), "batch_actor_user_tags")(
        ToolCallContext(write_deps), actor_ids=[actor_id], user_tag_ids=[999999]
    )
    assert "error" in unknown

    # update_actor 属 MANUAL 写入, 改过的字段自动上锁; 只有 set_actor_locks 能释放.
    await tool_fn(build_actor_ops_capability(), "update_actor")(
        ToolCallContext(write_deps), actor_id=actor_id, patch={"overview": "bio"}
    )
    locked = await write_deps.repo.get_actor(actor_id)
    assert locked is not None and "overview" in locked.locked_fields
    released = await tool_fn(build_actor_ops_capability(), "set_actor_locks")(
        ToolCallContext(write_deps), actor_id=actor_id, fields=[]
    )
    assert released == TOOL_OK
    row = await write_deps.repo.get_actor(actor_id)
    assert row is not None and row.locked_fields == []
    wrong = await tool_fn(build_actor_ops_capability(), "set_actor_locks")(
        ToolCallContext(write_deps), actor_id=999999, fields=[]
    )
    assert "error" in wrong


@pytest.mark.asyncio
async def test_saved_query_rename_and_delete(write_deps: AgentDeps) -> None:
    query = await write_deps.repo.create_saved_query(
        name="旧名", sql="SELECT 1", entity=SavedQueryEntity.DATA, persisted=True
    )
    assert query.id is not None
    write_deps.executor.cache.put(CachedResult(saved_query_id=query.id, sql=query.sql, columns=["x"], rows=[[1]]))
    cap = build_saved_query_ops_capability()

    renamed = await tool_fn(cap, "update_saved_query")(
        ToolCallContext(write_deps), query_id=query.id, name=" 新名 ", description=" 说明 "
    )
    assert renamed == TOOL_OK
    row = await write_deps.repo.get_saved_query(query.id)
    assert row is not None and (row.name, row.description) == ("新名", "说明")
    for kwargs in ({"name": "   "}, {}):
        rejected = await tool_fn(cap, "update_saved_query")(ToolCallContext(write_deps), query_id=query.id, **kwargs)
        assert "error" in rejected
    missing = await tool_fn(cap, "update_saved_query")(ToolCallContext(write_deps), query_id=999999, name="x")
    assert "error" in missing

    # 与 REST 的请求模型同一条上界: 工具不成为绕过校验的第三条写入路径.
    for kwargs in (
        {"name": "x" * (SAVED_QUERY_NAME_MAX + 1)},
        {"description": "x" * (SAVED_QUERY_DESCRIPTION_MAX + 1)},
    ):
        rejected = await tool_fn(cap, "update_saved_query")(ToolCallContext(write_deps), query_id=query.id, **kwargs)
        assert "error" in rejected
    boundary = await tool_fn(cap, "update_saved_query")(
        ToolCallContext(write_deps),
        query_id=query.id,
        name="x" * SAVED_QUERY_NAME_MAX,
        description="y" * SAVED_QUERY_DESCRIPTION_MAX,
    )
    assert boundary == TOOL_OK

    too_many = await tool_fn(cap, "delete_saved_queries")(
        ToolCallContext(write_deps, tool_call_approved=True), ids=list(range(SAVED_QUERY_IDS_MAX + 1))
    )
    assert "error" in too_many

    with pytest.raises(ApprovalRequired) as exc:
        await tool_fn(cap, "delete_saved_queries")(ToolCallContext(write_deps, tool_call_id="tc-sq"), ids=[query.id])
    assert (exc.value.metadata or {})["tool"] == "delete_saved_queries"
    deleted = await tool_fn(cap, "delete_saved_queries")(
        ToolCallContext(write_deps, tool_call_id="tc-sq", tool_call_approved=True), ids=[query.id, 999999]
    )
    assert deleted == {"deleted": 1, "missing": 1}
    assert await write_deps.repo.get_saved_query(query.id) is None
    assert write_deps.executor.cache.get(query.id, query.sql) is None


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
    caps = build_capabilities()
    return {name for cap in caps for name in cap_toolset(cap).tools}


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
