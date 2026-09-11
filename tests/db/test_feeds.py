"""Feed and FeedItem repository contract tests."""

from datetime import UTC, datetime

import pytest

from amane.db.models import FeedItemReadState, FeedItemState, TaskType
from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("state", "expected_keys"),
    [
        (FeedItemState.ACTIVE, ["active"]),
        (FeedItemState.IGNORED, ["ignored"]),
        (FeedItemState.ALL, ["ignored", "active"]),
    ],
)
async def test_list_feed_items_state_filters(repo: Repository, state: FeedItemState, expected_keys: list[str]) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/feed.xml")
    assert feed.id is not None
    active = await repo.create_feed_item(feed.id, "active", title="Alpha", number="AAA-001")
    ignored = await repo.create_feed_item(feed.id, "ignored", title="Beta", number="BBB-002")
    await repo.ignore_feed_items(feed.id, [ignored.id or 0])

    rows, total = await repo.list_feed_items(feed.id, state=state)

    assert total == len(expected_keys)
    assert [item.item_key for item, _ in rows] == expected_keys
    assert active.ignored_at is None


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("search", "expected_keys"),
    [
        ("AAA", ["key-only"]),
        ("UniqueTitle", ["key-only"]),
        ("unique.example/item", ["key-only"]),
        ("key-only", ["key-only"]),
        ("inside-html", ["key-only"]),
        ("", ["number", "title", "link", "other", "key-only"]),
        ("   ", ["number", "title", "link", "other", "key-only"]),
    ],
)
async def test_list_feed_items_search(repo: Repository, search: str, expected_keys: list[str]) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/search.xml")
    assert feed.id is not None
    await repo.create_feed_item(
        feed.id,
        "key-only",
        title="UniqueTitle",
        link="https://unique.example/item",
        description="<p>inside-html body</p>",
        number="AAA-001",
    )
    await repo.create_feed_item(feed.id, "other", title="Other", number="BBB-002")
    await repo.create_feed_item(feed.id, "link", link="https://link.example/only")
    await repo.create_feed_item(feed.id, "title", title="TitleOnly")
    await repo.create_feed_item(feed.id, "number", number="NumberOnly-001")

    rows, total = await repo.list_feed_items(feed.id, search=search, state=FeedItemState.ALL)

    assert total == len(expected_keys)
    assert [item.item_key for item, _ in rows] == expected_keys


@pytest.mark.asyncio(loop_scope="function")
async def test_list_feed_items_rejects_unknown_state(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/state.xml")
    assert feed.id is not None

    with pytest.raises(ValueError, match="Unknown feed item state"):
        await repo.list_feed_items(feed.id, state="unexpected")


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("state", "read", "expected_keys"),
    [
        (FeedItemState.ACTIVE, FeedItemReadState.ALL, ["read-active", "unread-active"]),
        (FeedItemState.ACTIVE, FeedItemReadState.UNREAD, ["unread-active"]),
        (FeedItemState.ACTIVE, FeedItemReadState.READ, ["read-active"]),
        (FeedItemState.IGNORED, FeedItemReadState.UNREAD, ["unread-ignored"]),
        (FeedItemState.IGNORED, FeedItemReadState.READ, ["read-ignored"]),
        (FeedItemState.ALL, FeedItemReadState.UNREAD, ["unread-ignored", "unread-active"]),
        (FeedItemState.ALL, FeedItemReadState.READ, ["read-ignored", "read-active"]),
        (FeedItemState.ALL, FeedItemReadState.ALL, ["read-ignored", "unread-ignored", "read-active", "unread-active"]),
    ],
)
async def test_list_feed_items_read_filters(
    repo: Repository, state: FeedItemState, read: FeedItemReadState, expected_keys: list[str]
) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/read.xml")
    assert feed.id is not None
    unread_active = await repo.create_feed_item(feed.id, "unread-active")
    read_active = await repo.create_feed_item(feed.id, "read-active")
    unread_ignored = await repo.create_feed_item(feed.id, "unread-ignored")
    read_ignored = await repo.create_feed_item(feed.id, "read-ignored")
    assert read_active.id is not None and unread_ignored.id is not None and read_ignored.id is not None
    await repo.mark_feed_items_read(feed.id, [read_active.id, read_ignored.id])
    await repo.ignore_feed_items(feed.id, [unread_ignored.id, read_ignored.id])

    rows, total = await repo.list_feed_items(feed.id, state=state, read=read)

    assert total == len(expected_keys)
    assert [item.item_key for item, _ in rows] == expected_keys
    assert unread_active.read_at is None


@pytest.mark.asyncio(loop_scope="function")
async def test_list_feed_items_rejects_unknown_read_state(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/read-state.xml")
    assert feed.id is not None

    with pytest.raises(ValueError, match="Unknown feed item read state"):
        await repo.list_feed_items(feed.id, read="unexpected")


@pytest.mark.asyncio(loop_scope="function")
async def test_feed_item_batch_actions_are_scoped_and_idempotent(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/actions.xml")
    other_feed = await repo.create_feed(name="other", url="https://example.com/other.xml")
    assert feed.id is not None and other_feed.id is not None
    first = await repo.create_feed_item(feed.id, "first")
    second = await repo.create_feed_item(feed.id, "second")
    foreign = await repo.create_feed_item(other_feed.id, "foreign")
    assert first.id is not None and second.id is not None and foreign.id is not None

    affected, missing = await repo.ignore_feed_items(feed.id, [first.id, first.id, foreign.id, 9999])
    assert (affected, missing) == (1, 2)

    rows, _ = await repo.list_feed_items(feed.id, state=FeedItemState.IGNORED)
    assert len(rows) == 1
    assert rows[0][0].id == first.id
    assert rows[0][0].ignored_at is not None

    affected, missing = await repo.ignore_feed_items(feed.id, [first.id])
    assert (affected, missing) == (1, 0)

    affected, missing = await repo.unignore_feed_items(feed.id, [first.id, second.id])
    assert (affected, missing) == (2, 0)

    affected, missing = await repo.delete_feed_items(feed.id, [second.id, foreign.id, 9999])
    assert (affected, missing) == (1, 2)
    rows, total = await repo.list_feed_items(feed.id, state=FeedItemState.ALL)
    assert total == 1
    assert [item.item_key for item, _ in rows] == ["first"]
    other_rows, other_total = await repo.list_feed_items(other_feed.id, state=FeedItemState.ALL)
    assert other_total == 1
    assert other_rows[0][0].id == foreign.id


@pytest.mark.asyncio(loop_scope="function")
async def test_feed_item_read_actions_are_scoped_and_idempotent(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/read-actions.xml")
    other_feed = await repo.create_feed(name="other", url="https://example.com/read-other.xml")
    assert feed.id is not None and other_feed.id is not None
    first = await repo.create_feed_item(feed.id, "first")
    second = await repo.create_feed_item(feed.id, "second")
    foreign = await repo.create_feed_item(other_feed.id, "foreign")
    assert first.id is not None and second.id is not None and foreign.id is not None

    affected, missing = await repo.mark_feed_items_read(feed.id, [first.id, first.id, foreign.id, 9999])
    assert (affected, missing) == (1, 2)

    rows, _ = await repo.list_feed_items(feed.id, state=FeedItemState.ALL, read=FeedItemReadState.READ)
    assert len(rows) == 1
    assert rows[0][0].id == first.id
    assert rows[0][0].read_at is not None

    affected, missing = await repo.mark_feed_items_read(feed.id, [first.id])
    assert (affected, missing) == (1, 0)

    affected, missing = await repo.mark_feed_items_unread(feed.id, [first.id, second.id])
    assert (affected, missing) == (2, 0)
    rows, total = await repo.list_feed_items(feed.id, state=FeedItemState.ALL, read=FeedItemReadState.UNREAD)
    assert total == 2
    assert {item.item_key for item, _ in rows} == {"first", "second"}
    _, other_total = await repo.list_feed_items(other_feed.id, state=FeedItemState.ALL, read=FeedItemReadState.READ)
    assert other_total == 0
    remaining_rows, _ = await repo.list_feed_items(other_feed.id, state=FeedItemState.ALL)
    assert remaining_rows[0][0].id == foreign.id
    assert remaining_rows[0][0].read_at is None


@pytest.mark.asyncio(loop_scope="function")
async def test_list_feed_items_group_prefix_and_ungrouped(repo: Repository) -> None:
    jav = await repo.create_feed(name="a", url="https://example.com/a.xml", group="jav/rsshub")
    jav_root = await repo.create_feed(name="b", url="https://example.com/b.xml", group="jav")
    other = await repo.create_feed(name="c", url="https://example.com/c.xml", group="news")
    bare = await repo.create_feed(name="d", url="https://example.com/d.xml", group="")
    assert jav.id is not None and jav_root.id is not None
    assert other.id is not None and bare.id is not None
    await repo.create_feed_item(jav.id, "jav-child")
    await repo.create_feed_item(jav_root.id, "jav-root")
    await repo.create_feed_item(other.id, "news")
    await repo.create_feed_item(bare.id, "bare")

    rows, total = await repo.list_feed_items(group="jav", state=FeedItemState.ALL)
    assert total == 2
    assert {item.item_key for item, _ in rows} == {"jav-child", "jav-root"}

    rows, total = await repo.list_feed_items(group="jav/rsshub", state=FeedItemState.ALL)
    assert total == 1
    assert rows[0][0].item_key == "jav-child"

    rows, total = await repo.list_feed_items(group="", state=FeedItemState.ALL)
    assert total == 1
    assert rows[0][0].item_key == "bare"

    rows, total = await repo.list_feed_items(state=FeedItemState.ALL)
    assert total == 4


@pytest.mark.asyncio(loop_scope="function")
async def test_list_feed_items_orders_by_published_at_then_created_at(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/dated.xml")
    assert feed.id is not None
    older = datetime(2024, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 6, 1, tzinfo=UTC)
    await repo.create_feed_item(feed.id, "undated")
    await repo.create_feed_item(feed.id, "old", published_at=older)
    await repo.create_feed_item(feed.id, "new", published_at=newer)

    rows, total = await repo.list_feed_items(feed.id, state=FeedItemState.ALL)

    assert total == 3
    # undated 回退 created_at (现在), 再按 published_at 新→旧
    assert [item.item_key for item, _ in rows] == ["undated", "new", "old"]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("offset", "limit", "expected_keys"),
    [
        (0, 2, ["c", "b"]),
        (2, 2, ["a"]),
        (3, 2, []),
        (0, 50, ["c", "b", "a"]),
    ],
)
async def test_list_feed_items_paginates_in_published_order(
    repo: Repository, offset: int, limit: int, expected_keys: list[str]
) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/paged.xml")
    assert feed.id is not None
    await repo.create_feed_item(feed.id, "a", published_at=datetime(2024, 1, 1, tzinfo=UTC))
    await repo.create_feed_item(feed.id, "b", published_at=datetime(2025, 1, 1, tzinfo=UTC))
    await repo.create_feed_item(feed.id, "c", published_at=datetime(2026, 1, 1, tzinfo=UTC))

    rows, total = await repo.list_feed_items(feed.id, offset=offset, limit=limit, state=FeedItemState.ALL)

    assert total == 3
    assert [item.item_key for item, _ in rows] == expected_keys


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("item_number", "meta_number", "expect_match"),
    [
        ("MIDV-123", "MIDV-123", True),
        ("midv-123", "MIDV-123", True),
        ("MIDV-123", "midv-123", True),
        (None, "MIDV-123", False),
        ("", "MIDV-123", False),
        ("OTHER-001", "MIDV-123", False),
    ],
)
async def test_list_feed_items_joins_metadata_case_insensitively(
    repo: Repository, item_number: str | None, meta_number: str, expect_match: bool
) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/join.xml")
    assert feed.id is not None
    await repo.create_feed_item(feed.id, "item", number=item_number)
    meta = await repo.upsert_metadata(number=meta_number)
    assert meta.id is not None

    rows, total = await repo.list_feed_items(feed.id, state=FeedItemState.ALL)

    assert total == 1
    assert len(rows) == 1
    _item, metadata_id = rows[0]
    assert (metadata_id == meta.id) is expect_match


@pytest.mark.asyncio(loop_scope="function")
async def test_list_feed_items_same_number_share_metadata_id(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/dup-number.xml")
    assert feed.id is not None
    await repo.create_feed_item(feed.id, "first", number="ABC-001")
    await repo.create_feed_item(feed.id, "second", number="abc-001")
    meta = await repo.upsert_metadata(number="ABC-001")
    assert meta.id is not None

    rows, total = await repo.list_feed_items(feed.id, state=FeedItemState.ALL)

    assert total == 2
    assert [metadata_id for _, metadata_id in rows] == [meta.id, meta.id]


@pytest.mark.asyncio(loop_scope="function")
async def test_create_tasks_batch_is_atomic_and_returns_ids(repo: Repository) -> None:
    tasks = await repo.create_tasks(TaskType.SCRAPE, [{"number": "A-001"}, {"number": "B-002"}])

    assert len(tasks) == 2
    assert all(task.id is not None for task in tasks)
    assert [task.payload["number"] for task in tasks] == ["A-001", "B-002"]


@pytest.mark.asyncio(loop_scope="function")
async def test_unread_counts_exclude_read_and_ignored(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/counts.xml")
    other = await repo.create_feed(name="other", url="https://example.com/counts-other.xml")
    assert feed.id is not None and other.id is not None
    unread = await repo.create_feed_item(feed.id, "unread")
    read = await repo.create_feed_item(feed.id, "read")
    ignored = await repo.create_feed_item(feed.id, "ignored")
    await repo.create_feed_item(other.id, "other-unread")
    assert read.id is not None and ignored.id is not None
    await repo.mark_feed_items_read(feed.id, [read.id])
    await repo.ignore_feed_items(feed.id, [ignored.id])

    counts = await repo.count_unread_feed_items()
    assert counts == {feed.id: 1, other.id: 1}
    assert await repo.count_unread_feed_items(feed.id) == {feed.id: 1}
    assert unread.read_at is None


@pytest.mark.asyncio(loop_scope="function")
async def test_updating_ignore_keywords_ignores_matching_active_items(repo: Repository) -> None:
    feed = await repo.create_feed(name="feed", url="https://example.com/keywords.xml")
    assert feed.id is not None
    keep = await repo.create_feed_item(feed.id, "keep", title="Regular title")
    hit = await repo.create_feed_item(feed.id, "hit", title="超豪华合集 VOL.1")
    already = await repo.create_feed_item(feed.id, "already", title="另一部合集")
    assert hit.id is not None and already.id is not None
    await repo.ignore_feed_items(feed.id, [already.id])

    updated = await repo.update_feed(feed.id, ignore_keywords=["合集"])
    assert updated is not None
    assert updated.ignore_keywords == ["合集"]

    rows, _ = await repo.list_feed_items(feed.id, state=FeedItemState.ALL)
    by_key = {item.item_key: item for item, _ in rows}
    assert by_key["keep"].ignored_at is None
    assert by_key["hit"].ignored_at is not None
    assert by_key["already"].ignored_at is not None
    assert keep.ignored_at is None
