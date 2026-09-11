"""按每源 interval 拉取 RSS/Atom; auto_enqueue 时入队 by-number SCRAPE."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from ..db.feed_keywords import item_matches_ignore_keywords
from ..db.models import TaskType
from ..handlers.models import build_feed_scrape_payload
from ..net.errors import RequestError
from ..net.http import WebClient
from ..parsing import extract_number
from .rss import ParsedFeedEntry, parse_feed_bytes

if TYPE_CHECKING:
    from ..db.models import Feed
    from ..db.repository import Repository

logger = structlog.get_logger()

_CHECK_INTERVAL = 60.0
_OK_NOT_MODIFIED = frozenset({304})
_SCRAPE_PRIORITY = -1


def apply_number_pattern(pattern: str, *texts: str | None) -> str | None:
    """用源配置的正则从文本中取番号; 有捕获组用 group 1, 否则整次匹配."""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    for text in texts:
        if not text:
            continue
        match = compiled.search(text)
        if match is None:
            continue
        if match.lastindex:
            group = match.group(1)
            stripped = group.strip() if group else ""
            return stripped or None
        return match.group(0).strip() or None
    return None


def resolve_entry_number(feed: Feed, entry: ParsedFeedEntry) -> str | None:
    """有 number_pattern 则仅用该正则, 否则 extract_number."""
    if feed.number_pattern:
        return apply_number_pattern(feed.number_pattern, entry.title, entry.description, entry.link)
    return extract_number(entry.title) or (extract_number(entry.description) if entry.description else None)


class FeedService:
    """到期 Feed 拉取与去重; auto_enqueue 时按源顺序入队, 同番号取最新一条."""

    def __init__(self, repo: Repository, web_client: WebClient) -> None:
        self._repo = repo
        self._web = web_client
        self._running = False
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def set_web_client(self, web_client: WebClient) -> None:
        self._web = web_client

    async def _feed_lock(self, feed_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(feed_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[feed_id] = lock
            return lock

    async def start(self) -> None:
        self._running = True
        logger.info("feed service started", check_interval=_CHECK_INTERVAL)
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("feed service tick error")
            await asyncio.sleep(_CHECK_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        logger.info("feed service stopped")

    async def _tick(self) -> None:
        now = datetime.now(UTC)
        due = await self._repo.list_due_feeds(now)
        for feed in due:
            if feed.id is None:
                continue
            await self.poll_one(feed.id)

    async def poll_one(self, feed_id: int) -> None:
        lock = await self._feed_lock(feed_id)
        async with lock:
            await self._poll_locked(feed_id)

    async def _poll_locked(self, feed_id: int) -> None:
        feed = await self._repo.get_feed(feed_id)
        if feed is None:
            return

        headers: dict[str, str] = {}
        if feed.etag:
            headers["If-None-Match"] = feed.etag
        if feed.last_modified:
            headers["If-Modified-Since"] = feed.last_modified

        now = datetime.now(UTC)
        next_fetch = now + timedelta(seconds=feed.interval_seconds)

        try:
            resp = await self._web.request(
                "GET",
                feed.url,
                headers=headers or None,
                ok_statuses=_OK_NOT_MODIFIED,
            )
        except RequestError as exc:
            logger.warning("feed fetch failed", feed_id=feed_id, url=feed.url, error=exc.message)
            await self._repo.update_feed(
                feed_id,
                last_error=exc.message,
                last_fetched_at=now,
                next_fetch_at=next_fetch,
            )
            return

        if resp.status_code == 304:
            await self._repo.update_feed(
                feed_id,
                last_error=None,
                last_fetched_at=now,
                next_fetch_at=next_fetch,
            )
            logger.info("feed not modified", feed_id=feed_id, url=feed.url)
            return

        try:
            body = resp.content
        except Exception:
            body = b""
        if not body:
            await self._repo.update_feed(
                feed_id,
                last_error="empty body",
                last_fetched_at=now,
                next_fetch_at=next_fetch,
            )
            return

        parsed = parse_feed_bytes(body)
        if parsed is None:
            await self._repo.update_feed(
                feed_id,
                last_error="不是合法的 RSS/Atom",
                last_fetched_at=now,
                next_fetch_at=next_fetch,
            )
            return

        entries = parsed.entries

        seen = await self._repo.list_feed_item_keys(feed_id)
        description_backfill: dict[str, str] = {}
        published_backfill: dict[str, datetime] = {}
        # 列表按 published_at 排. 无日期时回退 created_at; 同秒碰撞则靠 id, 所以无日期条目按旧→新写入.
        # 入队仍按源顺序, 同番号取最新一条.
        new_entries: list[tuple[ParsedFeedEntry, str | None]] = []
        for entry in entries:
            if entry.item_key in seen:
                if entry.description:
                    description_backfill[entry.item_key] = entry.description
                if entry.published_at is not None:
                    published_backfill[entry.item_key] = entry.published_at
                continue
            number = resolve_entry_number(feed, entry)
            new_entries.append((entry, number))
            seen.add(entry.item_key)
            if number is None:
                logger.warning(
                    "feed item has no number",
                    feed_id=feed_id,
                    item_key=entry.item_key,
                    title=entry.title,
                )

        ignored_keys: set[str] = set()
        for entry, number in reversed(new_entries):
            ignored = item_matches_ignore_keywords(
                feed.ignore_keywords or [],
                title=entry.title,
                number=number,
                description=entry.description,
                item_key=entry.item_key,
            )
            await self._repo.create_feed_item(
                feed_id,
                entry.item_key,
                title=entry.title or None,
                link=entry.link,
                description=entry.description,
                number=number,
                published_at=entry.published_at,
                ignored=ignored,
            )
            if ignored:
                ignored_keys.add(entry.item_key)

        enqueued_numbers: set[str] = set()
        enqueued = 0
        if feed.auto_enqueue:
            for _entry, number in new_entries:
                if number is None or number in enqueued_numbers or _entry.item_key in ignored_keys:
                    continue
                enqueued_numbers.add(number)
                await self._repo.create_task(
                    task_type=TaskType.SCRAPE,
                    payload=build_feed_scrape_payload(feed, number),
                    priority=_SCRAPE_PRIORITY,
                )
                enqueued += 1

        await self._repo.backfill_feed_item_snapshots(
            feed_id, descriptions=description_backfill, published_at=published_backfill
        )

        etag = resp.headers.get("ETag") or resp.headers.get("etag")
        last_modified = resp.headers.get("Last-Modified") or resp.headers.get("last-modified")
        title = parsed.title if not feed.name.strip() else None
        await self._repo.update_feed(
            feed_id,
            last_error=None,
            last_fetched_at=now,
            next_fetch_at=next_fetch,
            last_enqueued=enqueued,
            etag=etag if isinstance(etag, str) else feed.etag,
            last_modified=last_modified if isinstance(last_modified, str) else feed.last_modified,
            name=title if title is not None else feed.name,
        )
        logger.info("feed polled", feed_id=feed_id, url=feed.url, entries=len(entries), enqueued=enqueued)
