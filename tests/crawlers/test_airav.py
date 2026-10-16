"""airav 只认站内写法 ``FC2-PPV-<id>``, 其余番号用原样写法.

线上实录: 管线产出的 FC2 番号不带 PPV (``FC2-<id>``), 用该写法检索站内 0 条; 换成 ``FC2-PPV-<id>``
命中. 假出站通道按 URL 子串路由, 未声明的 URL 一律失败, 因此检索词的形态与顺序都被断言.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.airav import AiravCrawler
from amane.net.errors import RequestError

from .driven import http_client

# 站内条目标题以番号开头, 详情链接的 hid 与番号不同形.
HIT = """<html><body>
<div class="oneVideo"><a href="/video?hid=99-22-03696"></a><h5>FC2-PPV-3061625 人生初拍攝。</h5></div>
</body></html>"""
EMPTY = """<html><body><div class="no-result">0</div></body></html>"""

Route = Callable[[str], str]


def _ok(text: str) -> Route:
    def route(_url: str) -> str:
        return text

    return route


def _fake_web(routes: dict[str, Route]) -> AsyncMock:
    """URL 含某键即走该路由; 未声明的 URL 判失败."""
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        for pattern, route in routes.items():
            if pattern in url:
                return route(url)
        raise RequestError(url, "no mock matched")

    web.get_text.side_effect = get_text
    return web


SEARCH_CASES: list[tuple[str, dict[str, Route], str | None]] = [
    # 管线产出的形态: 首个检索词换成站内写法, 用一个请求命中
    ("FC2-3061625", {"kw=FC2-PPV-3061625": _ok(HIT)}, "https://airav.io/video?hid=99-22-03696"),
    # 入参已是站内写法: 形态重复, 只发一次请求
    ("FC2-PPV-3061625", {"kw=FC2-PPV-3061625": _ok(HIT)}, "https://airav.io/video?hid=99-22-03696"),
    # 站内写法查不到时退回原样写法
    (
        "FC2-3061625",
        {"kw=FC2-PPV-3061625": _ok(EMPTY), "kw=FC2-3061625": _ok(HIT)},
        "https://airav.io/video?hid=99-22-03696",
    ),
    # 非 FC2 番号只用原样写法
    ("SSIS-497", {"kw=SSIS-497": _ok(EMPTY)}, None),
    # 站内只有相近番号: 不取首条
    (
        "FC2-3061625",
        {"kw=FC2-PPV-3061625": _ok(HIT.replace("3061625", "3061620")), "kw=FC2-3061625": _ok(EMPTY)},
        None,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "routes", "expected_url"), SEARCH_CASES)
async def test_search_uses_site_spelling(query: str, routes: dict[str, Route], expected_url: str | None) -> None:
    crawler = AiravCrawler(client=http_client(_fake_web(routes)))
    assert await crawler._search(SearchQuery(query)) == expected_url
