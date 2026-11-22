"""caribbeancom 的 HTTP 状态与请求次数判据, 数据驱动覆盖不到的部分.

解析、早年条目缺字段与站点自报番号不符都由 TOML 用例覆盖; 这里只断言 TOML 表达不了的两类:
状态码到「未命中 / 失败」的映射, 以及一次抓取只发一次请求.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.caribbeancom import CaribbeanComCrawler
from amane.net.errors import FailureKind, RequestError, RequestFailure, SourceError

URL = "https://www.caribbeancom.com/moviepages/112226-001/index.html"
PAGE = """<html><body><div class="movie-info section">
<h1 itemprop="name">タイトル</h1>
<script>var Movie = {"movie_id" : '112226-001'};</script>
</div></body></html>"""

Route = Callable[[str], str]


def _raises(status: int) -> Route:
    def route(url: str) -> str:
        raise RequestError(url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}"))

    return route


def _crawler(route: Route) -> CaribbeanComCrawler:
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        return route(url)

    web.get_text.side_effect = get_text
    return CaribbeanComCrawler(client=HttpClient(web=web))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "expected_reason"),
    [
        # 未知番号: 站点 404, 与「没有这部片」同义.
        (_raises(404), None),
        # 其余失败照旧上报.
        (_raises(500), "server_error"),
        (_raises(429), "rate_limited"),
    ],
)
async def test_http_status_maps_to_miss_or_failure(route: Route, expected_reason: str | None) -> None:
    crawler = _crawler(route)

    if expected_reason is None:
        assert await crawler.fetch(SearchQuery(number="112226-001")) is None
        return
    with pytest.raises(SourceError) as excinfo:
        await crawler.fetch(SearchQuery(number="112226-001"))
    assert excinfo.value.reason == expected_reason


@pytest.mark.asyncio
async def test_hit_requests_detail_page_once() -> None:
    """一次抓取只发一次请求."""
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        return PAGE

    web.get_text.side_effect = get_text
    crawler = CaribbeanComCrawler(client=HttpClient(web=web))

    assert await crawler.fetch(SearchQuery(number="112226-001")) is not None
    assert web.get_text.await_count == 1
    assert web.get_text.await_args.args == (URL,)
