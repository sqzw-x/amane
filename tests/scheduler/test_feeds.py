"""FeedService / RSS 解析 / 番号提取契约."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from warnings import catch_warnings, filterwarnings

import pytest

from amane.db.models import TaskType
from amane.db.repository import Repository
from amane.net.errors import FailureKind, RequestError, RequestFailure
from amane.parsing import ContentType
from amane.scheduler.feeds import FeedService, apply_number_pattern, resolve_entry_number
from amane.scheduler.rss import ParsedFeedEntry, parse_feed_bytes


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


RSS_TWO_ITEMS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <guid>g-midv</guid>
      <title>[4K] MIDV-123 Hello</title>
      <link>https://example.com/1</link>
      <description><![CDATA[<p>Cover <b>MIDV-123</b></p>]]></description>
    </item>
    <item>
      <guid>g-none</guid>
      <title>今週の新作をお届けします</title>
    </item>
    <item>
      <guid>g-dup</guid>
      <title>MIDV-123 again</title>
    </item>
  </channel>
</rss>
""".encode()

ATOM_ONE = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom</title>
  <entry>
    <id>atom-1</id>
    <title>SSIS-456 Atom</title>
    <link href="https://example.com/a"/>
  </entry>
</feed>
"""


class FakeResp:
    def __init__(self, status_code: int, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeWeb:
    def __init__(self, resp: FakeResp | None = None, failure: RequestFailure | None = None) -> None:
        self.resp = resp
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        data: object | None = None,
        json: object | None = None,
        use_proxy: bool = True,
        timeout: float | None = None,
        allow_redirects: bool = True,
        ok_statuses: frozenset[int] | None = None,
    ) -> FakeResp:
        self.calls.append({"method": method, "url": url, "headers": headers, "ok_statuses": ok_statuses})
        if self.failure is not None or self.resp is None:
            raise RequestError(url, self.failure)
        return self.resp


def _service(repo: Repository, web: FakeWeb) -> FeedService:
    # FakeWeb 与 WebClient 结构兼容; 测试替身不走真实 TLS.
    return FeedService(repo, web)  # pyright: ignore[reportArgumentType]


def test_parse_rss_and_atom():
    rss = parse_feed_bytes(RSS_TWO_ITEMS)
    assert rss is not None
    assert rss.title == "Test"
    assert [e.item_key for e in rss.entries] == ["g-midv", "g-none", "g-dup"]
    assert rss.entries[0].description == "<p>Cover <b>MIDV-123</b></p>"
    assert rss.entries[0].published_at is None
    atom = parse_feed_bytes(ATOM_ONE)
    assert atom is not None
    assert atom.title == "Atom"
    assert atom.entries[0].item_key == "atom-1"
    assert "SSIS-456" in atom.entries[0].title
    assert atom.entries[0].published_at is None


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (
            b"""<?xml version="1.0"?><rss version="2.0"><channel>
            <item><guid>rss</guid><title>t</title>
            <pubDate>Mon, 01 Jan 2024 12:30:00 GMT</pubDate></item>
            </channel></rss>""",
            datetime(2024, 1, 1, 12, 30, tzinfo=UTC),
        ),
        (
            b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
            <entry><id>atom</id><title>t</title>
            <published>2024-06-15T08:00:00Z</published>
            <updated>2025-01-01T00:00:00Z</updated></entry>
            </feed>""",
            datetime(2024, 6, 15, 8, 0, tzinfo=UTC),
        ),
        (
            b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
            <entry><id>upd</id><title>t</title>
            <updated>2025-02-02T03:04:05Z</updated></entry>
            </feed>""",
            datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC),
        ),
        (
            b"""<?xml version="1.0"?><rss version="2.0"><channel>
            <item><guid>bad</guid><title>t</title><pubDate>not-a-date</pubDate></item>
            </channel></rss>""",
            None,
        ),
    ],
)
def test_parse_entry_published_at(xml: bytes, expected: datetime | None):
    with catch_warnings():
        filterwarnings("error", message=".*issue 310.*", category=DeprecationWarning)
        parsed = parse_feed_bytes(xml)
    assert parsed is not None
    assert parsed.entries[0].published_at == expected


def test_parse_feed_bytes_garbage():
    assert parse_feed_bytes(b"not xml at all {{{") is None
    assert parse_feed_bytes(b"") is None


@pytest.mark.parametrize(
    ("pattern", "texts", "expected"),
    [
        (r"(MIDV-\d+)", ("hello MIDV-123 world",), "MIDV-123"),
        (r"MIDV-\d+", ("MIDV-999",), "MIDV-999"),
        (r"(SSIS-\d+)", ("nope", "SSIS-1 in desc"), "SSIS-1"),
        (r"(MISSING)", ("MIDV-123",), None),
    ],
)
def test_apply_number_pattern(pattern: str, texts: tuple[str, ...], expected: str | None):
    assert apply_number_pattern(pattern, *texts) == expected


def test_apply_number_pattern_invalid_regex():
    assert apply_number_pattern(r"(", "MIDV-123") is None


def test_resolve_entry_number_pattern_skips_builtin():
    feed = MagicMock()
    feed.number_pattern = r"(CUSTOM-\d+)"
    entry = ParsedFeedEntry(item_key="1", title="MIDV-123 CUSTOM-9", link=None, description=None)
    assert resolve_entry_number(feed, entry) == "CUSTOM-9"


@pytest.mark.asyncio
async def test_poll_history_follows_feed_newest_first(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml", auto_enqueue=False)
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)

    rows, total = await repo.list_feed_items(feed.id, state="all")
    assert total == 3
    assert [item.item_key for item, _ in rows] == ["g-midv", "g-none", "g-dup"]


@pytest.mark.asyncio
async def test_poll_auto_enqueue_false_records_without_scrape(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml", auto_enqueue=False)
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)

    assert [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE] == []
    _, total = await repo.list_feed_items(feed.id)
    assert total == 3
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.last_error is None
    assert refreshed.last_enqueued == 0
    assert refreshed.next_fetch_at is not None


@pytest.mark.asyncio
async def test_enabling_auto_enqueue_does_not_replay_seen_items(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml", auto_enqueue=False)
    assert feed.id is not None
    service = _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS)))
    await service.poll_one(feed.id)
    await repo.update_feed(feed.id, auto_enqueue=True)
    await service.poll_one(feed.id)
    assert [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE] == []


@pytest.mark.asyncio
async def test_poll_enqueues_unique_numbers(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS, {"ETag": '"abc"'}))
    service = _service(repo, web)
    await service.poll_one(feed.id)

    tasks = await repo.list_tasks()
    scrape = [t for t in tasks if t.type == TaskType.SCRAPE]
    assert len(scrape) == 1
    assert scrape[0].priority == -1
    payload = scrape[0].payload
    assert payload["number"] == "MIDV-123"
    assert payload["media_file_id"] is None
    assert set(payload["use_cache"]) == {"metadata", "trans"}

    rows, total = await repo.list_feed_items(feed.id, offset=0, limit=50)
    items = [item for item, _ in rows]
    assert total == 3
    none_item = next(i for i in items if i.item_key == "g-none")
    assert none_item.number is None
    midv = next(i for i in items if i.item_key == "g-midv")
    assert midv.description == "<p>Cover <b>MIDV-123</b></p>"

    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.last_error is None
    assert refreshed.last_enqueued == 1
    assert refreshed.etag == '"abc"'
    assert refreshed.next_fetch_at is not None


@pytest.mark.asyncio
async def test_poll_skips_seen_item_keys(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS))
    service = _service(repo, web)
    await service.poll_one(feed.id)
    await service.poll_one(feed.id)
    tasks = [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE]
    assert len(tasks) == 1
    _, total = await repo.list_feed_items(feed.id)
    assert total == 3


@pytest.mark.asyncio
async def test_ignored_item_remains_deduplicated(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/ignored.xml")
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS))
    service = _service(repo, web)

    await service.poll_one(feed.id)
    rows, _ = await repo.list_feed_items(feed.id, state="all")
    item = next(item for item, _ in rows if item.item_key == "g-midv")
    assert item.id is not None
    await repo.ignore_feed_items(feed.id, [item.id])

    await service.poll_one(feed.id)

    tasks = [task for task in await repo.list_tasks() if task.type == TaskType.SCRAPE]
    assert len(tasks) == 1
    _, active_total = await repo.list_feed_items(feed.id)
    _, ignored_total = await repo.list_feed_items(feed.id, state="ignored")
    assert active_total == 2
    assert ignored_total == 1


@pytest.mark.asyncio
async def test_deleted_item_is_discovered_again(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/deleted.xml")
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS))
    service = _service(repo, web)

    await service.poll_one(feed.id)
    rows, _ = await repo.list_feed_items(feed.id, state="all")
    item = next(item for item, _ in rows if item.item_key == "g-midv")
    assert item.id is not None
    affected, missing = await repo.delete_feed_items(feed.id, [item.id])
    assert (affected, missing) == (1, 0)

    await service.poll_one(feed.id)

    tasks = [task for task in await repo.list_tasks() if task.type == TaskType.SCRAPE]
    assert len(tasks) == 2
    _, total = await repo.list_feed_items(feed.id, state="all")
    assert total == 3


@pytest.mark.asyncio
async def test_poll_304_no_enqueue(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    await repo.update_feed(feed.id, etag='"x"')
    web = FakeWeb(FakeResp(304))
    service = _service(repo, web)
    await service.poll_one(feed.id)
    assert [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE] == []
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.last_error is None
    assert web.calls[0]["headers"] == {"If-None-Match": '"x"'}
    assert web.calls[0]["ok_statuses"] == frozenset({304})


@pytest.mark.asyncio
async def test_poll_http_failure_sets_error(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    web = FakeWeb(resp=None, failure=RequestFailure(kind=FailureKind.CURL, message="boom"))
    service = _service(repo, web)
    await service.poll_one(feed.id)
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.last_error == "boom"
    assert [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE] == []


@pytest.mark.asyncio
async def test_poll_invalid_xml(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, b"not a feed"))
    service = _service(repo, web)
    await service.poll_one(feed.id)
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.last_error == "不是合法的 RSS/Atom"


@pytest.mark.asyncio
async def test_content_type_and_use_cache_per_feed(repo: Repository):
    feed = await repo.create_feed(
        name="t",
        url="https://example.com/rss.xml",
        content_type=ContentType.WESTERN,
        use_cache=[],
    )
    assert feed.id is not None
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS))
    service = _service(repo, web)
    await service.poll_one(feed.id)
    task = next(t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE)
    assert task.payload["content_type"] == "western"
    assert task.payload["use_cache"] == []


@pytest.mark.asyncio
async def test_number_pattern_only(repo: Repository):
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><guid>1</guid><title>MIDV-123 CUSTOM-42 extra</title></item>
    </channel></rss>"""
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml", number_pattern=r"(CUSTOM-\d+)")
    assert feed.id is not None
    service = _service(repo, FakeWeb(FakeResp(200, xml)))
    await service.poll_one(feed.id)
    task = next(t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE)
    assert task.payload["number"] == "CUSTOM-42"


@pytest.mark.asyncio
async def test_tick_skips_not_due(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    future = datetime.now(UTC) + timedelta(hours=1)
    await repo.update_feed(feed.id, next_fetch_at=future)
    web = FakeWeb(FakeResp(200, RSS_TWO_ITEMS))
    service = _service(repo, web)
    await service._tick()
    assert web.calls == []


@pytest.mark.asyncio
async def test_disabled_not_due(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml", enabled=False)
    assert feed.id is not None
    due = await repo.list_due_feeds(datetime.now(UTC))
    assert due == []


@pytest.mark.asyncio
async def test_poll_fills_empty_name_from_feed_title(repo: Repository):
    feed = await repo.create_feed(name="", url="https://example.com/rss.xml")
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.name == "Test"


@pytest.mark.asyncio
async def test_poll_keeps_explicit_name(repo: Repository):
    feed = await repo.create_feed(name="Mine", url="https://example.com/rss.xml")
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)
    refreshed = await repo.get_feed(feed.id)
    assert refreshed is not None
    assert refreshed.name == "Mine"


@pytest.mark.asyncio
async def test_delete_feed_cascades_items(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/rss.xml")
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)
    _, total = await repo.list_feed_items(feed.id)
    assert total == 3
    assert await repo.delete_feed(feed.id) is True
    assert await repo.get_feed(feed.id) is None
    _, total = await repo.list_feed_items(feed.id)
    assert total == 0


def test_parse_atom_html_content():
    atom = parse_feed_bytes(
        b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2000/Atom">
  <title>Atom</title>
  <entry>
    <id>atom-html</id>
    <title>Body</title>
    <content type="html">&lt;p&gt;Atom body&lt;/p&gt;</content>
  </entry>
</feed>
"""
    )
    assert atom is not None
    assert atom.entries[0].description == "<p>Atom body</p>"


@pytest.mark.asyncio
async def test_poll_backfills_empty_description(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/backfill.xml", auto_enqueue=False)
    assert feed.id is not None
    await repo.create_feed_item(feed.id, "g-midv", title="old")
    await _service(repo, FakeWeb(FakeResp(200, RSS_TWO_ITEMS))).poll_one(feed.id)
    rows, total = await repo.list_feed_items(feed.id, state="all")
    assert total == 3
    by_key = {item.item_key: item for item, _ in rows}
    assert by_key["g-midv"].description == "<p>Cover <b>MIDV-123</b></p>"
    tasks = [t for t in await repo.list_tasks() if t.type == TaskType.SCRAPE]
    assert tasks == []


RSS_DATED_OLDEST_FIRST = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Dated</title>
    <item>
      <guid>old</guid>
      <title>OLD-001</title>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
    </item>
    <item>
      <guid>new</guid>
      <title>NEW-002</title>
      <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.mark.asyncio
async def test_poll_orders_history_by_pubdate_not_document_order(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/dated.xml", auto_enqueue=False)
    assert feed.id is not None
    await _service(repo, FakeWeb(FakeResp(200, RSS_DATED_OLDEST_FIRST))).poll_one(feed.id)
    rows, total = await repo.list_feed_items(feed.id, state="all")
    assert total == 2
    assert [item.item_key for item, _ in rows] == ["new", "old"]
    assert _as_utc(rows[0][0].published_at) == datetime(2025, 1, 1, tzinfo=UTC)
    assert _as_utc(rows[1][0].published_at) == datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_poll_backfills_empty_published_at(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/dated.xml", auto_enqueue=False)
    assert feed.id is not None
    await repo.create_feed_item(feed.id, "new", title="old-title")
    await _service(repo, FakeWeb(FakeResp(200, RSS_DATED_OLDEST_FIRST))).poll_one(feed.id)
    rows, total = await repo.list_feed_items(feed.id, state="all")
    assert total == 2
    by_key = {item.item_key: item for item, _ in rows}
    assert _as_utc(by_key["new"].published_at) == datetime(2025, 1, 1, tzinfo=UTC)
    assert by_key["new"].title == "old-title"


@pytest.mark.asyncio
async def test_poll_does_not_overwrite_published_at(repo: Repository):
    feed = await repo.create_feed(name="t", url="https://example.com/dated.xml", auto_enqueue=False)
    assert feed.id is not None
    original = datetime(2020, 1, 1, tzinfo=UTC)
    await repo.create_feed_item(feed.id, "new", title="kept", published_at=original)
    await _service(repo, FakeWeb(FakeResp(200, RSS_DATED_OLDEST_FIRST))).poll_one(feed.id)
    rows, _ = await repo.list_feed_items(feed.id, state="all")
    by_key = {item.item_key: item for item, _ in rows}
    assert _as_utc(by_key["new"].published_at) == original
