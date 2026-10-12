"""giga 的年龄确认会话: 领取时机, 站内 Referer, 会话失效后的重领与重试, 与零结果页的区分.

假出站通道按序回放两个通道 (低层 request 领会话, get_text 取页), 夹具是真实响应, 不打网络.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from curl_cffi.requests import Headers

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.giga import GigaCrawler, _is_age_gate, _issued_session
from amane.net.errors import FailureReason, RequestError, SourceError
from amane.observability.replay import _ReplayResponse

from .driven import load_response_file

CASES = Path(__file__).parent / "cases" / "giga"
ENTRY = "https://www.giga-web.jp/cookie_set.php"
SEARCH = "https://www.giga-web.jp/search/?keyword=THPA-17"
EMPTY = "https://www.giga-web.jp/search/?keyword=ZZZZ-99999"
PRODUCT = "https://www.giga-web.jp/product/index.php?product_id=7784"
REFERER = {"Referer": "https://www.giga-web.jp/"}
# 入口的两种应答 (线上实录): 下发新会话, 以及只删掉请求带去的旧会话.
ISSUED = "PHPSESSID=8d61pqv5k7n2t9fr4b0h3c1m; path=/; secure; SameSite=none"
DELETED = "PHPSESSID=deleted; expires=Thu, 01-Jan-1970 00:00:01 GMT; Max-Age=0; path=/; domain=www.giga-web.jp"


def _fixture(name: str) -> str:
    text = load_response_file(CASES, name)
    assert isinstance(text, str)
    return text


def _entry(set_cookie: str) -> tuple[str, str, str]:
    """一次入口调用, 响应带给定的 ``Set-Cookie``."""
    return ("request", ENTRY, set_cookie)


def _fetch(url_needle: str, body: str) -> tuple[str, str, str]:
    """一次取页, URL 含 ``url_needle`` 时回 ``body``."""
    return ("get_text", url_needle, body)


def _fake_web(steps: list[tuple[str, str, str]]) -> AsyncMock:
    """按序回放两个通道的假出站通道; 调用顺序、通道与 URL 都要与 ``steps`` 对上, 多出的请求直接失败."""
    web = AsyncMock()
    pending = list(steps)

    def take(channel: str, url: str) -> str:
        assert pending, f"多出的 {channel} 请求: {url}"
        index = len(steps) - len(pending) + 1
        step_channel, needle, payload = pending[0]
        assert step_channel == channel, f"第 {index} 次请求走了 {channel} 通道: {url}"
        assert needle in url, f"第 {index} 次请求的 URL 不含 {needle!r}: {url}"
        pending.pop(0)
        return payload

    async def get_text(url: str, **kwargs: object) -> str:
        return take("get_text", url)

    async def request(method: str, url: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(headers=Headers([("set-cookie", take("request", url))]))

    web.get_text.side_effect = get_text
    web.request.side_effect = request
    return web


def _calls(web: AsyncMock) -> tuple[list[str], list[str]]:
    return (
        [call.args[0] for call in web.get_text.call_args_list],
        [call.args[1] for call in web.request.call_args_list],
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("thpa17_search.html", False),
        ("zzzz99999_empty_search.html", False),
        ("thpa17_detail.html", False),
        ("age_gate_search.html", True),
    ],
)
def test_age_gate_discriminant(filename: str, expected: bool) -> None:
    """判据只认入口链接: 年龄页带它, 检索页 / 零结果页 / 商品页都不带."""
    assert _is_age_gate(_fixture(filename)) is expected


def test_issued_session_accepts_replay_response() -> None:
    """离线回放的响应替代实现与 WebClient 的响应头同型: 没有 Set-Cookie 时按没领到处理, 不抛异常."""
    resp = _ReplayResponse(status_code=302, content=b"", headers=Headers({"Content-Type": "text/html"}))
    assert _issued_session(resp.headers) is False


@pytest.mark.asyncio
async def test_entry_failure_propagates_without_retry() -> None:
    """入口调用在通道层失败时上抛, 不忽略也不重试入口: 再请求一次会把该响应刚下发的会话删掉."""
    web = AsyncMock()
    web.request.side_effect = RequestError(ENTRY, "入口未带 Location")
    crawler = GigaCrawler(client=HttpClient(web=web))

    with pytest.raises(RequestError):
        await crawler._search(SearchQuery("THPA-17"))
    assert _calls(web) == ([], [ENTRY])


@pytest.mark.asyncio
async def test_first_search_claims_session_once() -> None:
    """会话没领过时检索前领一次; 之后的检索不再访问入口."""
    page = _fixture("thpa17_search.html")
    web = _fake_web([_entry(ISSUED), _fetch(SEARCH, page), _fetch(SEARCH, page)])
    crawler = GigaCrawler(client=HttpClient(web=web))

    assert await crawler._search(SearchQuery("THPA-17")) == PRODUCT
    assert await crawler._search(SearchQuery("THPA-17")) == PRODUCT
    assert _calls(web) == ([SEARCH, SEARCH], [ENTRY])


@pytest.mark.asyncio
async def test_stale_session_is_reclaimed_until_issued() -> None:
    """罐里留着旧会话时入口只删掉它, 再领一次才拿到可用会话 (先 GET 过站点的进程就是这个序列)."""
    web = _fake_web([_entry(DELETED), _entry(ISSUED), _fetch(SEARCH, _fixture("thpa17_search.html"))])
    crawler = GigaCrawler(client=HttpClient(web=web))

    assert await crawler._search(SearchQuery("THPA-17")) == PRODUCT
    assert _calls(web) == ([SEARCH], [ENTRY, ENTRY])


@pytest.mark.asyncio
async def test_intra_site_requests_carry_referer() -> None:
    """入口, 检索与商品页都带站内 Referer."""
    web = _fake_web(
        [
            _entry(ISSUED),
            _fetch(SEARCH, _fixture("thpa17_search.html")),
            _fetch(PRODUCT, _fixture("thpa17_detail.html")),
        ]
    )
    crawler = GigaCrawler(client=HttpClient(web=web))

    url = await crawler._search(SearchQuery("THPA-17"))
    assert url == PRODUCT
    assert await crawler._scrape(url) is not None
    headers: list[Any] = [call.kwargs["headers"] for call in web.get_text.call_args_list]
    headers += [call.kwargs["headers"] for call in web.request.call_args_list]
    assert headers == [REFERER] * 3


@pytest.mark.asyncio
async def test_age_page_is_reclaimed_and_retried_once() -> None:
    """会话失效后检索命中年龄页: 重领会话并重试一次, 重试成功即返回商品页."""
    page = _fixture("thpa17_search.html")
    age = _fixture("age_gate_search.html")
    web = _fake_web(
        [
            _entry(ISSUED),
            _fetch(SEARCH, page),
            _fetch(SEARCH, age),
            _entry(DELETED),
            _entry(ISSUED),
            _fetch(SEARCH, page),
        ]
    )
    crawler = GigaCrawler(client=HttpClient(web=web))

    assert await crawler._search(SearchQuery("THPA-17")) == PRODUCT
    assert await crawler._search(SearchQuery("THPA-17")) == PRODUCT
    assert _calls(web) == ([SEARCH, SEARCH, SEARCH], [ENTRY, ENTRY, ENTRY])


@pytest.mark.asyncio
async def test_age_page_after_reclaim_fails_with_age_verification() -> None:
    """重领后仍是年龄页: 只重试一次, 并按 age_verification 上报, 不混同于没有作品."""
    age = _fixture("age_gate_search.html")
    web = _fake_web([_entry(ISSUED), _fetch(SEARCH, age), _entry(ISSUED), _fetch(SEARCH, age)])
    crawler = GigaCrawler(client=HttpClient(web=web))

    with pytest.raises(SourceError) as excinfo:
        await crawler._search(SearchQuery("THPA-17"))
    assert excinfo.value.reason is FailureReason.AGE_VERIFICATION
    assert _calls(web) == ([SEARCH, SEARCH], [ENTRY, ENTRY])


@pytest.mark.asyncio
async def test_empty_result_page_is_not_reclaimed() -> None:
    """零结果页判为空结果: 不重领会话, 也不重试."""
    web = _fake_web([_entry(ISSUED), _fetch(EMPTY, _fixture("zzzz99999_empty_search.html"))])
    crawler = GigaCrawler(client=HttpClient(web=web))

    assert await crawler._search(SearchQuery("ZZZZ-99999")) is None
    assert _calls(web) == ([EMPTY], [ENTRY])
