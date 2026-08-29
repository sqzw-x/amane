"""feed-ops Capability 表测试."""

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
from amane.agent.feed_ops import AgentFeedCreate, AgentFeedItemBatch, AgentFeedUpdate, build_feed_ops_capability
from amane.agent.sql import ReadonlySqlSandbox
from amane.agent.tools import AgentDeps
from amane.agent.trace import TraceEvent
from amane.api.models.feeds import FeedItemBatchAction
from amane.db.models import FeedItemState
from amane.db.repository import Repository
from amane.parsing import ContentType


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
    toolset = build_feed_ops_capability().get_toolset()
    assert toolset is not None
    return cast(FunctionToolset[AgentDeps], toolset)


def _tool_fn(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    return cast(Callable[..., Awaitable[dict[str, Any]]], _toolset().tools[name].function)


@pytest_asyncio.fixture
async def feed_deps(tmp_path: Path, repo: Repository) -> AgentDeps:
    db = tmp_path / "ops.db"
    async with aiosqlite.connect(db) as conn:
        await conn.execute("CREATE TABLE metadata (id INTEGER PRIMARY KEY, title TEXT)")
        await conn.commit()
    session = await repo.create_agent_session(title="feed-ops")
    assert session.id is not None
    return AgentDeps(
        repo=repo,
        executor=QueryExecutor(ReadonlySqlSandbox(db), ResultCache(ttl_s=60, max_entries=8)),
        session_id=session.id,
        trace=_MemTrace(),  # type: ignore[arg-type]
        sql_timeout_ms=2000,
    )


def test_feed_ops_capability_contract() -> None:
    cap = build_feed_ops_capability()
    assert cap.id == "feed-ops"
    assert cap.defer_loading is True
    names = set(_toolset().tools)
    assert {
        "list_feeds",
        "get_feed",
        "create_feed",
        "update_feed",
        "poll_feed",
        "delete_feed",
        "list_feed_items",
        "batch_feed_items",
    } <= names


@pytest.mark.asyncio
async def test_create_update_and_poll_feed(feed_deps: AgentDeps) -> None:
    polled: list[int] = []

    async def poll(feed_id: int) -> None:
        polled.append(feed_id)

    feed_deps.bridge.poll_feed = poll
    create = await _tool_fn("create_feed")(
        _Ctx(feed_deps),
        request=AgentFeedCreate(
            name="  source  ",
            url=" https://example.com/feed.xml ",
            group=" jav // rsshub ",
            interval_seconds=600,
        ),
    )
    assert create["name"] == "source"
    assert create["group"] == "jav/rsshub"
    assert create["interval_seconds"] == 600
    feed_id = int(create["id"])
    assert polled == [feed_id]

    updated = await _tool_fn("update_feed")(
        _Ctx(feed_deps),
        feed_id=feed_id,
        patch=AgentFeedUpdate(
            enabled=False,
            auto_enqueue=False,
            content_type=ContentType.FC2,
            use_cache=set(),
        ),
    )
    assert updated["enabled"] is False
    assert updated["auto_enqueue"] is False
    assert updated["content_type"] == "fc2"
    assert updated["use_cache"] == []

    polled_now = await _tool_fn("poll_feed")(_Ctx(feed_deps), feed_id=feed_id)
    assert polled_now["id"] == feed_id
    assert polled == [feed_id, feed_id]


@pytest.mark.asyncio
async def test_feed_validation_and_missing_ids(feed_deps: AgentDeps) -> None:
    invalid = await _tool_fn("create_feed")(_Ctx(feed_deps), request=AgentFeedCreate(url="ftp://example.com/feed.xml"))
    assert "error" in invalid

    missing = await _tool_fn("get_feed")(_Ctx(feed_deps), feed_id=9999)
    assert missing == {"error": "feed 9999 不存在"}

    feed = await feed_deps.repo.create_feed(name="source", url="https://example.com/source.xml")
    assert feed.id is not None
    invalid_patch = await _tool_fn("update_feed")(_Ctx(feed_deps), feed_id=feed.id, patch=AgentFeedUpdate(enabled=None))
    assert invalid_patch == {"error": "enabled 不能为 null"}


@pytest.mark.asyncio
async def test_list_and_batch_feed_items(feed_deps: AgentDeps) -> None:
    feed = await feed_deps.repo.create_feed(
        name="source",
        url="https://example.com/source-items.xml",
        content_type=ContentType.WESTERN,
        use_cache=[],
    )
    other = await feed_deps.repo.create_feed(name="other", url="https://example.com/other-items.xml")
    assert feed.id is not None and other.id is not None
    first = await feed_deps.repo.create_feed_item(feed.id, "first", title="First", number="ABC-001")
    duplicate = await feed_deps.repo.create_feed_item(feed.id, "duplicate", number="abc-001")
    no_number = await feed_deps.repo.create_feed_item(feed.id, "no-number")
    foreign = await feed_deps.repo.create_feed_item(other.id, "foreign", number="XYZ-999")
    assert first.id is not None and duplicate.id is not None and no_number.id is not None and foreign.id is not None

    listed = await _tool_fn("list_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        state=FeedItemState.ALL,
        search="First",
    )
    assert listed["total"] == 1
    assert listed["items"][0]["item_key"] == "first"

    ignored = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.IGNORE, ids=[first.id, first.id, foreign.id, 9999]),
    )
    assert ignored == {
        "action": "ignore",
        "affected": 1,
        "missing": 2,
        "skipped": 0,
        "submitted": 0,
        "task_ids": [],
    }

    scraped = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.SCRAPE, ids=[first.id, duplicate.id, no_number.id]),
    )
    assert scraped["affected"] == 3
    assert scraped["missing"] == 0
    assert scraped["skipped"] == 1
    assert scraped["submitted"] == 1
    assert len(scraped["task_ids"]) == 1
    tasks = await feed_deps.repo.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].payload == {
        "number": "ABC-001",
        "content_type": "western",
        "media_file_id": None,
        "use_cache": [],
    }


@pytest.mark.asyncio
async def test_delete_feed_and_items_require_approval(feed_deps: AgentDeps) -> None:
    feed = await feed_deps.repo.create_feed(name="source", url="https://example.com/delete.xml")
    assert feed.id is not None
    item = await feed_deps.repo.create_feed_item(feed.id, "item")
    assert item.id is not None

    with pytest.raises(ApprovalRequired):
        await _tool_fn("batch_feed_items")(
            _Ctx(feed_deps, tool_call_id="tc-items"),
            feed_id=feed.id,
            request=AgentFeedItemBatch(action=FeedItemBatchAction.DELETE, ids=[item.id]),
        )
    assert feed_deps.pending["tc-items"].extra["action"] == "delete"

    deleted_item = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps, tool_call_id="tc-items", tool_call_approved=True),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.DELETE, ids=[item.id]),
    )
    assert deleted_item["affected"] == 1

    with pytest.raises(ApprovalRequired):
        await _tool_fn("delete_feed")(_Ctx(feed_deps, tool_call_id="tc-feed"), feed_id=feed.id)
    deleted_feed = await _tool_fn("delete_feed")(
        _Ctx(feed_deps, tool_call_id="tc-feed", tool_call_approved=True), feed_id=feed.id
    )
    assert deleted_feed["deleted"] is True
