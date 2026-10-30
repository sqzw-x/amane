"""指定网址刮削: 来源锁定, 番号来自页面, 类型推断与缓存语义."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from amane.aggregate import CrawlerLike
from amane.config import HotSettings
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.models import FetchOptions, MediaMetadata, SearchQuery
from amane.enums import SiteName
from amane.handlers import ScrapeHandler, ScrapePayload
from amane.parsing import ContentType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from amane.db.repository import Repository


class UrlCrawler(Crawler):
    """只允许按网址取详情页; 检索被调用即失败."""

    def __init__(self, metadata: MediaMetadata):
        self._profile = self.profile()
        self.name = self._profile.name
        self._metadata = metadata
        self.scraped_urls: list[str] = []

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://javdb.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        raise AssertionError("网址刮削不得检索番号")

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        self.scraped_urls.append(url)
        return self._metadata


class RecordingFactory:
    def __init__(self, crawlers: dict[str, CrawlerLike]):
        self._crawlers = crawlers
        self.requested: list[str] | None = None

    async def get_crawlers(self, names: Iterable[str]) -> dict[str, CrawlerLike]:
        self.requested = list(names)
        return {name: self._crawlers[name] for name in names if name in self._crawlers}


def _handler(repo: Repository, crawler: UrlCrawler) -> tuple[ScrapeHandler, RecordingFactory]:
    factory = RecordingFactory({"javdb": crawler})
    return ScrapeHandler(repo, factory, AsyncMock(), HotSettings()), factory


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_uses_page_number(repo: Repository):
    """只请求网址所属来源, 跳过检索, 番号与类型来自页面."""
    crawler = UrlCrawler(MediaMetadata(number="MIDV-123", title="From URL"))
    handler, factory = _handler(repo, crawler)

    result = await handler.handle(ScrapePayload(url="https://javdb.com/v/abcd1"))

    assert result.success is True
    assert factory.requested == ["javdb"]
    assert crawler.scraped_urls == ["https://javdb.com/v/abcd1"]
    metadata = await repo.get_metadata_by_number("MIDV-123")
    assert metadata is not None
    assert metadata.title == "From URL"
    assert metadata.content_type is ContentType.CENSORED


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_keeps_explicit_content_type(repo: Repository):
    """填写的内容类型优先于按页面番号推断."""
    crawler = UrlCrawler(MediaMetadata(number="MIDV-123", title="From URL"))
    handler, _ = _handler(repo, crawler)

    result = await handler.handle(ScrapePayload(url="https://javdb.com/v/abcd1", content_type=ContentType.WESTERN))

    assert result.success is True
    metadata = await repo.get_metadata_by_number("MIDV-123")
    assert metadata is not None
    assert metadata.content_type is ContentType.WESTERN


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_rejects_page_without_number(repo: Repository):
    """页面没有番号时无法确定条目, 不入库."""
    crawler = UrlCrawler(MediaMetadata(number="", title="No number"))
    handler, _ = _handler(repo, crawler)

    result = await handler.handle(ScrapePayload(url="https://javdb.com/v/abcd1"))

    assert result.success is False
    assert result.error is not None
    assert "识别番号" in result.error


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_rejects_unknown_source(repo: Repository):
    """网址不属于任何来源时不构造爬虫."""
    crawler = UrlCrawler(MediaMetadata(number="MIDV-123"))
    handler, factory = _handler(repo, crawler)

    result = await handler.handle(ScrapePayload(url="https://unknown.example/v/1"))

    assert result.success is False
    assert result.error is not None
    assert "不属于" in result.error
    assert factory.requested is None


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_attaches_media_file(repo: Repository):
    """媒体库的指定刮削把页面结果关联到该文件."""
    crawler = UrlCrawler(MediaMetadata(number="MIDV-123", title="From URL"))
    handler, _ = _handler(repo, crawler)
    media = await repo.create_media_file(library_id=1, path="/media/UNKNOWN.mp4")
    assert media.id is not None

    result = await handler.handle(ScrapePayload(url="https://javdb.com/v/abcd1", media_file_id=media.id))

    assert result.success is True
    assert result.result is not None
    refreshed = await repo.get_media_file(media_id=media.id)
    assert refreshed is not None
    assert refreshed.metadata_id == result.result.metadata_id


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_url_ignores_metadata_cache(repo: Repository):
    """网址刮削抓完才知道番号, 因此不复用该番号的站点快照."""
    seeded = await repo.upsert_metadata(
        number="MIDV-123",
        title="Old",
        raw={"javdb": {"number": "MIDV-123", "title": "Old"}},
    )
    assert seeded.id is not None
    crawler = UrlCrawler(MediaMetadata(number="MIDV-123", title="Fresh"))
    handler, _ = _handler(repo, crawler)

    result = await handler.handle(ScrapePayload(url="https://javdb.com/v/abcd1"))

    assert result.success is True
    assert crawler.scraped_urls == ["https://javdb.com/v/abcd1"]
    metadata = await repo.get_metadata_by_number("MIDV-123")
    assert metadata is not None
    assert metadata.title == "Fresh"


@pytest.mark.parametrize(
    "payload",
    [
        {"number": "MIDV-123", "url": "https://javdb.com/v/abcd1"},
        {"number": "", "url": None},
        {"url": "ftp://javdb.com/v/abcd1"},
        {"url": "javdb.com/v/abcd1"},
    ],
)
def test_payload_rejects_invalid_target(payload: dict[str, object]):
    with pytest.raises(ValidationError):
        ScrapePayload.model_validate(payload)


def test_payload_allows_url_with_media_file():
    payload = ScrapePayload.model_validate({"url": "https://javdb.com/v/abcd1", "media_file_id": 1})
    assert payload.media_file_id == 1
