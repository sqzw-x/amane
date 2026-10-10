"""检索别名在 javdb 检索链路上的效果: 别名命中要真的被接受并取到条目 URL."""

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.javdb import JavDBCrawler

_ITEM = """
<a class="box" href="/v/abc123">
  <div class="video-title"><strong>{number}</strong></div>
</a>
"""
_NO_ITEM = '<div class="empty-message">沒有找到相關內容</div>'


def _result_page(number: str) -> str:
    return f"<html><body>{_ITEM.format(number=number)}</body></html>"


class _StubClient:
    """按检索词返回预置页面, 并记下实际请求过的检索词."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.terms: list[str] = []

    def as_http_client(self) -> HttpClient:
        return AsyncMock(spec=HttpClient, get_html=AsyncMock(side_effect=self.get_html))

    async def get_html(self, url: str, **_kwargs) -> str:
        term = url.split("q=", 1)[1].split("&", 1)[0]
        self.terms.append(term)
        return self.pages.get(term, _NO_ITEM)


@pytest.mark.asyncio
async def test_alias_term_is_accepted_and_used() -> None:
    """站内条目写的是别名形态 (`MIUM-123`), 入参是本地番号 (`300MIUM-123`)."""
    client = _StubClient({"MIUM-123": _result_page("MIUM-123")})
    crawler = JavDBCrawler(client=client.as_http_client())

    url = await crawler._search(SearchQuery("300MIUM-123", alternate_numbers=("MIUM-123",)))

    assert url == "https://javdb.com/v/abc123"
    assert client.terms == ["300MIUM-123", "MIUM-123"]


@pytest.mark.asyncio
async def test_alias_term_ignored_without_alternates() -> None:
    """没有别名时同名条目不会被接受 (避免放宽判定把无关条目也放进来)."""
    client = _StubClient({"300MIUM-123": _result_page("MIUM-123")})
    crawler = JavDBCrawler(client=client.as_http_client())

    assert await crawler._search(SearchQuery("300MIUM-123")) is None


@pytest.mark.asyncio
async def test_primary_number_is_still_accepted() -> None:
    client = _StubClient({"300MIUM-123": _result_page("300MIUM-123")})
    crawler = JavDBCrawler(client=client.as_http_client())

    url = await crawler._search(SearchQuery("300MIUM-123", alternate_numbers=("MIUM-123",)))

    assert url == "https://javdb.com/v/abc123"
    assert client.terms == ["300MIUM-123"]
