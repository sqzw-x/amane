"""airav 检索与详情里的站内番号写法.

线上实录: 部分来源把来源名写在站内番号前 (``caribbeancom_051318-665`` / ``1pondo_051025_001``),
按标题首个词直接比对会漏掉这些条目; FC2 则只认站内写法 ``FC2-PPV-<id>``, 管线产出的 ``FC2-<id>``
需要改写检索词. 假出站通道按 URL 子串路由, 未声明的 URL 一律失败, 因此检索词的形态, 顺序与请求次数
都被断言 — 真实响应用例只能断言最终命中, 覆盖不到这些.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.airav import AiravCrawler
from amane.net.errors import RequestError

from .driven import http_client

HIT = "https://airav.io/video?hid=99-22-03696"
# 站内条目标题以番号开头, 详情链接的 hid 与番号不同形.
HIT_PAGE = """<html><body>
<div class="oneVideo"><a href="/video?hid=99-22-03696"></a><h5>FC2-PPV-3061625 人生初拍攝。</h5></div>
</body></html>"""
EMPTY_PAGE = """<html><body><div class="no-result">0</div></body></html>"""
CARIBBEAN_HIT = "https://airav.io/video?hid=99-07-16387"
CARIBBEAN_PAGE = """<html><body>
<div class="oneVideo"><a href="/video?hid=99-07-16387"></a><h5>caribbeancom_051318-665 好色M女童顏正妹  姬川優奈</h5></div>
</body></html>"""
PONDO_HIT = "https://airav.io/video?hid=QC-BT-4306373"
PONDO_PAGE = """<html><body>
<div class="oneVideo"><a href="/video?hid=QC-BT-4306373"></a><h5>1pondo_051025_001 名模精選 圓山希</h5></div>
</body></html>"""
MUSUME_HIT = "https://airav.io/video?hid=QC-BT-4306374"
MUSUME_PAGE = """<html><body>
<div class="oneVideo"><a href="/video?hid=QC-BT-4306374"></a><h5>10musume_102926_01 秘藏小穴精選</h5></div>
</body></html>"""

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


SEARCH_CASES: list[tuple[str, dict[str, Route], str | None, int]] = [
    # 管线产出的形态: 首个检索词换成站内写法, 一次请求命中
    ("FC2-3061625", {"kw=FC2-PPV-3061625": _ok(HIT_PAGE)}, HIT, 1),
    # 入参已是站内写法: 形态重复, 不重发同一检索词 (该形态在站内也没有)
    ("FC2-PPV-3061625", {"kw=FC2-PPV-3061625": _ok(EMPTY_PAGE)}, None, 1),
    # 站内写法落空后退回原样写法
    ("FC2-3061625", {"kw=FC2-PPV-3061625": _ok(EMPTY_PAGE), "kw=FC2-3061625": _ok(HIT_PAGE)}, HIT, 2),
    # 非 FC2 番号只用原样写法
    ("SSIS-497", {"kw=SSIS-497": _ok(EMPTY_PAGE)}, None, 1),
    # 站内只有相近番号: 不取首条
    (
        "FC2-3061625",
        {"kw=FC2-PPV-3061625": _ok(HIT_PAGE.replace("3061625", "3061620")), "kw=FC2-3061625": _ok(EMPTY_PAGE)},
        None,
        2,
    ),
    # 标题前缀的来源名不参与番号比对
    ("051318-665", {"kw=051318-665": _ok(CARIBBEAN_PAGE)}, CARIBBEAN_HIT, 1),
    ("051025_001", {"kw=051025_001": _ok(PONDO_PAGE)}, PONDO_HIT, 1),
    ("102926_01", {"kw=102926_01": _ok(MUSUME_PAGE)}, MUSUME_HIT, 1),
    # 剥除来源名后仍须全等, 相近番号不取
    (
        "051318-665",
        {"kw=051318-665": _ok(CARIBBEAN_PAGE.replace("051318-665", "051319-665"))},
        None,
        1,
    ),
    # 站内番号本身带来源名 (HEYZO) 时不剥
    ("HEYZO-3607", {"kw=HEYZO-3607": _ok(HIT_PAGE.replace("FC2-PPV-3061625", "HEYZO-3607"))}, HIT, 1),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "routes", "expected_url", "expected_calls"), SEARCH_CASES)
async def test_search_uses_site_spelling(
    query: str, routes: dict[str, Route], expected_url: str | None, expected_calls: int
) -> None:
    web = _fake_web(routes)
    crawler = AiravCrawler(client=http_client(web))
    assert await crawler._search(SearchQuery(query)) == expected_url
    assert web.get_text.await_count == expected_calls


CARIBBEAN_DETAIL = """<html><head>
<meta property="og:image" content="https://airav.io/storage/cover/big/99-07-16387.jpg">
</head><body>
<div class="video-title my-3"><h1>caribbeancom_051318-665 好色M女童顏正妹  姬川優奈</h1></div>
<div class="video-info"><p>姬川優奈回到最愛的男友身邊。</p></div>
<div class="video-item"><i class="fa fa-clock"></i>2018-05-13 00:00:00</div>
<a href="/actor?id=1">姬川優奈</a>
<a href="/tag?fid=1">加勒比</a>
<a href="/tag?tid=1">巨乳</a>
<li class="my-2">番號：<span>caribbeancom_051318-665</span></li>
</body></html>"""


@pytest.mark.asyncio
async def test_scrape_strips_source_label() -> None:
    web = AsyncMock()
    web.get_text.return_value = CARIBBEAN_DETAIL
    crawler = AiravCrawler(client=http_client(web))

    meta = await crawler._scrape(CARIBBEAN_HIT)

    assert meta is not None
    assert meta.number == "051318-665"
    assert meta.title == "好色M女童顏正妹  姬川優奈"
    assert [actor.name for actor in meta.actors] == ["姬川優奈"]
    assert meta.studio == "加勒比"
    assert meta.release == "2018-05-13"
    assert meta.tags == ["巨乳"]
    assert meta.plot == "姬川優奈回到最愛的男友身邊。"
    assert meta.thumb_urls == ["https://airav.io/storage/cover/big/99-07-16387.jpg"]
