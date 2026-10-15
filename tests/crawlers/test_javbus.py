"""javbus 检索端点的 404 与直达 404 同义, 判无此番号; 其余失败照旧上报.

线上实录: 检索词一条也匹配不上时站点的 ``/search/<词>`` 直接 404, 命中时回 200 的模糊结果页.
假出站通道按 URL 子串路由, 不打网络.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.javbus import JavBusCrawler
from amane.net.errors import FailureKind, RequestError, RequestFailure

from .driven import http_client

DETAIL = """<html><body>
<h3>GGH-009 さらEカップ 巨乳淫語未亡人</h3>
<a class="movie-box" href="/GGH-009"><div class="photo-info"><date>GGH-009</date></div></a>
</body></html>"""
# 检索页 200 但结果只有相近番号 (模糊匹配).
FUZZY = """<html><body>
<a class="movie-box" href="/JAC-078"><div class="photo-info"><date>JAC-078</date></div></a>
</body></html>"""

Route = Callable[[str], str]


def _ok(text: str) -> Route:
    def route(_url: str) -> str:
        return text

    return route


def _status(status: int) -> Route:
    def route(url: str) -> str:
        raise RequestError(url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}"))

    return route


def _fake_web(routes: dict[str, Route]) -> AsyncMock:
    """URL 含某键即走该路由; 路由抛异常即该请求失败, 未声明的 URL 一样失败."""
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        for pattern, route in routes.items():
            if pattern in url:
                return route(url)
        raise RequestError(url, "no mock matched")

    web.get_text.side_effect = get_text
    return web


SEARCH_CASES: list[tuple[str, dict[str, Route], str | None]] = [
    # 带数字段的入参: 该形态直达与检索都是 404, 改用去掉数字段的形态直达命中
    ("723GGH-009", {"723GGH-009": _status(404), "/GGH-009": _ok(DETAIL)}, "https://www.javbus.com/GGH-009"),
    # 两种形态站内都没有: 404 不等于来源失败
    ("390JAC-079", {"390JAC-079": _status(404), "/JAC-079": _status(404)}, None),
    # 检索页正常但只回相近番号: 同样判无此番号
    ("390JAC-079", {"390JAC-079": _status(404), "/JAC-079": _ok(FUZZY)}, None),
]

FAILURE_CASES: list[tuple[str, dict[str, Route], int]] = [
    ("390JAC-079", {"390JAC-079": _status(500), "/JAC-079": _status(500)}, 500),
    ("390JAC-079", {"390JAC-079": _status(503), "/JAC-079": _status(503)}, 503),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "routes", "expected_url"), SEARCH_CASES)
async def test_search_treats_site_404_as_miss(query: str, routes: dict[str, Route], expected_url: str | None) -> None:
    crawler = JavBusCrawler(client=http_client(_fake_web(routes)))
    assert await crawler._search(SearchQuery(query)) == expected_url


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "routes", "status"), FAILURE_CASES)
async def test_search_reports_other_failures(query: str, routes: dict[str, Route], status: int) -> None:
    crawler = JavBusCrawler(client=http_client(_fake_web(routes)))
    with pytest.raises(RequestError) as excinfo:
        await crawler._search(SearchQuery(query))
    assert excinfo.value.http_status == status
