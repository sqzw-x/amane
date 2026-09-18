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
from amane.agent.tools import TOOL_OK, AgentDeps
from amane.agent.trace import TraceEvent
from amane.api.models.feeds import FeedItemBatchAction
from amane.db.models import FeedItemReadState, FeedItemState
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
    feed_id = int(create["feed_id"])
    assert polled == [feed_id]
    stored = await feed_deps.repo.get_feed(feed_id)
    assert stored is not None
    assert stored.name == "source"
    assert stored.group == "jav/rsshub"
    assert stored.interval_seconds == 600
    assert stored.ignore_keywords == []

    updated = await _tool_fn("update_feed")(
        _Ctx(feed_deps),
        feed_id=feed_id,
        patch=AgentFeedUpdate(
            enabled=False,
            auto_enqueue=False,
            content_type=ContentType.FC2,
            use_cache=set(),
            ignore_keywords=[" 合集 ", "合集"],
        ),
    )
    assert updated == TOOL_OK
    stored = await feed_deps.repo.get_feed(feed_id)
    assert stored is not None
    assert stored.enabled is False
    assert stored.auto_enqueue is False
    assert stored.content_type == ContentType.FC2
    assert stored.use_cache == []
    assert stored.ignore_keywords == ["合集"]

    polled_now = await _tool_fn("poll_feed")(_Ctx(feed_deps), feed_id=feed_id)
    assert polled_now == TOOL_OK
    assert polled == [feed_id, feed_id]


@pytest.mark.asyncio
async def test_poll_feed_surfaces_fetch_error(feed_deps: AgentDeps) -> None:
    """FeedService 把抓取失败写进 last_error 而不是抛出, 工具须把它当失败回报."""

    async def poll(feed_id: int) -> None:
        await feed_deps.repo.update_feed(feed_id, last_error="connection reset")

    feed = await feed_deps.repo.create_feed(name="source", url="https://example.com/broken.xml")
    assert feed.id is not None
    feed_deps.bridge.poll_feed = poll
    assert await _tool_fn("poll_feed")(_Ctx(feed_deps), feed_id=feed.id) == {"error": "拉取失败: connection reset"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["last_error", "raise"])
async def test_create_feed_reports_initial_poll_failure(feed_deps: AgentDeps, failure: str) -> None:
    """首次拉取失败不得掩盖创建成功: 仍回新 feed id, 失败原因单列 ``poll_error``."""

    async def poll(feed_id: int) -> None:
        if failure == "raise":
            raise RuntimeError("connection reset")
        await feed_deps.repo.update_feed(feed_id, last_error="connection reset")

    feed_deps.bridge.poll_feed = poll
    out = await _tool_fn("create_feed")(
        _Ctx(feed_deps), request=AgentFeedCreate(name=f"src-{failure}", url=f"https://example.com/{failure}.xml")
    )
    assert out["poll_error"] == "订阅源已创建, 但首次拉取失败: connection reset"
    assert await feed_deps.repo.get_feed(int(out["feed_id"])) is not None


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
    assert listed["items"][0]["number"] == "ABC-001"
    assert listed["items"][0]["read"] is False
    assert listed["items"][0]["ignored"] is False

    read = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.READ, ids=[first.id, first.id, foreign.id, 9999]),
    )
    assert read == {"affected": 1, "missing": 2}
    unread_list = await _tool_fn("list_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        state=FeedItemState.ALL,
        read=FeedItemReadState.UNREAD,
    )
    assert unread_list["total"] == 2
    seen_list = await _tool_fn("list_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        state=FeedItemState.ALL,
        read=FeedItemReadState.READ,
    )
    assert seen_list["total"] == 1
    assert seen_list["items"][0]["number"] == "ABC-001"
    assert seen_list["items"][0]["read"] is True

    ignored = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.IGNORE, ids=[first.id, first.id, foreign.id, 9999]),
    )
    assert ignored == {"affected": 1, "missing": 2}

    scraped = await _tool_fn("batch_feed_items")(
        _Ctx(feed_deps),
        feed_id=feed.id,
        request=AgentFeedItemBatch(action=FeedItemBatchAction.SCRAPE, ids=[first.id, duplicate.id, no_number.id]),
    )
    assert scraped == {"affected": 3, "missing": 0, "skipped": 1, "submitted": 1}
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
    assert deleted_item == {"affected": 1, "missing": 0}

    with pytest.raises(ApprovalRequired):
        await _tool_fn("delete_feed")(_Ctx(feed_deps, tool_call_id="tc-feed"), feed_id=feed.id)
    deleted_feed = await _tool_fn("delete_feed")(
        _Ctx(feed_deps, tool_call_id="tc-feed", tool_call_approved=True), feed_id=feed.id
    )
    assert deleted_feed == TOOL_OK
    assert await feed_deps.repo.get_feed(feed.id) is None
