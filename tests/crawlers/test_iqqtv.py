"""iqqtv 的语言前缀与失效域名判定; 解析用例见 cases/iqqtv/ 下的数据驱动 TOML.

站点按 ``Accept-Language`` 判定访问者地区: 落在中国大陆以外时非中文取值对任何语言前缀都回 404 空页,
因此语言前缀与请求头分开断言. 域名失效后站点对任何路径都回同一个 200 跳转壳, 只有核对正文才能判出来源
不可用, 这也是连通性探测覆盖基类的原因.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import FetchOptions, SearchQuery
from amane.crawlers.sites.iqqtv import IqqtvCrawler
from amane.enums import Language
from amane.net.connectivity import ConnectivityStatus
from amane.net.errors import FailureReason

if TYPE_CHECKING:
    from amane.net.http import WebClient

_RESULTS = """<html><body><div class="row">
<span class="title"><a class="ga_name" href="{href}" title="标题 SOE-896">标题 SOE-896</a></span>
</div></body></html>"""

#: 正常页里也有窗口跳转做站内导航, 只有落到 /lander 的那一行是失效域名的跳转壳.
_NORMAL = """<html><head><title>iQQTV</title></head><body><script>
window.location.href = "/cn/stary/";
</script></body></html>"""
_LANDER = (
    "<!DOCTYPE html><html><head><script>window.onload=function(){"
    'window.location.href="/lander?kw=SOE-896"}</script></head></html>'
)


class _Web:
    """记下每次取页地址与请求头的假 WebClient."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get_text(self, url: str, **kwargs: object) -> str:
        headers = kwargs.get("headers")
        self.calls.append((url, dict(headers) if isinstance(headers, dict) else {}))
        return self.body


def _client(body: str) -> tuple[HttpClient, _Web]:
    web = _Web(body)
    return HttpClient(web=cast("WebClient", web)), web


# 站点只保留 /cn /jp /en 三个前缀 (默认为繁体中文); 详情页 href 自带前缀, 回填时按请求的语言前缀重建.
_HREF_CASES: list[tuple[str, Language | None, str]] = [
    ("/cn/player.php?uuid=u1&cat=19", None, "https://iqqbl.com/cn/player.php?uuid=u1&cat=19"),
    ("/cn/player.php?uuid=u1&cat=19", Language.ZH_TW, "https://iqqbl.com/player.php?uuid=u1&cat=19"),
    ("/jp/player.php?uuid=u1&cat=19", Language.JP, "https://iqqbl.com/jp/player.php?uuid=u1&cat=19"),
    ("/en/player.php?uuid=u1&cat=19", Language.EN, "https://iqqbl.com/en/player.php?uuid=u1&cat=19"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("href", "language", "expected"), _HREF_CASES)
async def test_search_rewrites_href_to_requested_language(href: str, language: Language | None, expected: str) -> None:
    client, web = _client(_RESULTS.format(href=href))

    url = await IqqtvCrawler(client=client)._search(
        SearchQuery("SOE-896"), FetchOptions(language) if language else None
    )

    assert url == expected
    assert web.calls[0][0].endswith("/search.php?kw=SOE-896")


# 非中文取值下站点连 /jp 与 /en 都回 404 空页, 因此四个语言前缀共用同一个中文取值.
@pytest.mark.asyncio
@pytest.mark.parametrize("language", list(Language))
async def test_every_request_carries_a_chinese_language_header(language: Language) -> None:
    client, web = _client(_RESULTS.format(href="/cn/player.php?uuid=u1&cat=19"))

    await IqqtvCrawler(client=client)._search(SearchQuery("SOE-896"), FetchOptions(language))

    locale = web.calls[0][1]["Accept-Language"]
    assert locale.split(",")[0].split(";")[0].casefold().startswith("zh")


# 探测落在真实入口 (检索页) 上并核对正文: 失效域名对任何路径都回 200, 只看状态码会误报可达.
_CONNECTIVITY_CASES: list[tuple[str, ConnectivityStatus, FailureReason | None]] = [
    (_NORMAL, ConnectivityStatus.OK, None),
    (_LANDER, ConnectivityStatus.FAILED, FailureReason.EMPTY_RESPONSE),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "status", "reason"), _CONNECTIVITY_CASES)
async def test_connectivity_inspects_body(body: str, status: ConnectivityStatus, reason: FailureReason | None) -> None:
    client, web = _client(body)

    outcome = await IqqtvCrawler(client=client).check_connectivity()

    assert (outcome.status, outcome.reason) == (status, reason)
    assert web.calls[0][0] == "https://iqqbl.com/cn/search.php"


# 破坏版 (克破 / 无码流出版) 在检索阶段被排除, 直达详情页时同样不允许产出元数据.
_EXCLUDED_TITLES: list[str] = [
    "【无码流出版】标题 SOE-896",
    "【無碼流出版】標題 SOE-896",
    "标题 克破 SOE-896",
    "标题 无码破解 SOE-896",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _EXCLUDED_TITLES)
async def test_scrape_rejects_broken_copies(title: str) -> None:
    client, _ = _client(f'<html><body><h1 class="h4 b">{title}</h1></body></html>')

    assert await IqqtvCrawler(client=client)._scrape("https://iqqbl.com/cn/player.php?uuid=u1&cat=19") is None
