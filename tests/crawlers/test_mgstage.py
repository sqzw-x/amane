"""mgstage 爬虫单元测试: 竖版海报推导, 商品目录索引的解析, 索引档的取舍与刷新时序."""

from __future__ import annotations

import asyncio
import gzip
import time
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest
from parsel import Selector

from amane.crawlers.http import HttpClient
from amane.crawlers.parsing import select_number_index
from amane.crawlers.sites.mgstage import (
    _SITEMAP_SHARDS,
    CatalogUnusable,
    MGStageCrawler,
    _poster_url,
    _sample_mp4,
    parse_catalog,
)
from amane.net.errors import FailureKind, RequestError, RequestFailure

_PID = "c5cc7c58-f209-4fab-b18b-53c9173e0292"

if TYPE_CHECKING:
    from amane.net.http import WebClient


def _urlset(*numbers: str) -> bytes:
    body = "".join(f"<url><loc>https://www.mgstage.com/product/product_detail/{n}/</loc></url>" for n in numbers)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    ).encode()


@pytest.mark.parametrize(
    ("cover_url", "expected"),
    [
        (
            "https://image.mgstage.com/images/prestige/abf/355/pb_e_abf-355.jpg",
            "https://image.mgstage.com/images/prestige/abf/355/pf_e_abf-355.jpg",
        ),
        (
            "https://image.mgstage.com/images/shirouto/siro/3759/pb_e_siro-3759.jpg",
            "https://image.mgstage.com/images/shirouto/siro/3759/pf_e_siro-3759.jpg",
        ),
        # 详情页的图片兜底位给的不是横版放大图, 这些形态不推导.
        ("https://image.mgstage.com/images/prestige/abf/355/pf_o1_abf-355.jpg", None),
        ("https://image.mgstage.com/images/shirouto/siro/3759/pb_p_siro-3759.jpg", None),
        ("pb_e_abf-355.jpg", None),
        ("", None),
    ],
)
def test_poster_url(cover_url: str, expected: str | None) -> None:
    assert _poster_url(cover_url) == expected


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # 开头带数字段: 去掉数字段后对齐裸番号写法.
        ("STKO-005", "107STKO-005"),
        ("107STKO-005", "107STKO-005"),
        # 裸番号在目录里也有条目时命中的是自己.
        ("ABF-355", "ABF-355"),
        ("SIRO-3759", "SIRO-3759"),
    ],
)
def test_catalog_keys_on_tail(number: str, expected: str | None) -> None:
    index = parse_catalog([_urlset("107STKO-005", "ABF-355", "SIRO-3759")])
    found = index.candidates(number)
    picked = select_number_index(found, number)
    assert (found[picked] if picked is not None else None) == expected


def test_catalog_keeps_ambiguous_entries() -> None:
    """同一尾号在站点上确有两部作品时两条都保留, 由调用方按入参取舍."""
    index = parse_catalog([_urlset("483SGK-004", "902SGK-004")])
    assert index.candidates("SGK-004") == ("483SGK-004", "902SGK-004")
    assert index.candidates("483SGK-004") == ("483SGK-004", "902SGK-004")
    # 入参写着数字段时能精确命中其中一条; 没写时两条都是候选, 无法确认.
    assert select_number_index(index.candidates("483SGK-004"), "483SGK-004") == 0
    assert select_number_index(index.candidates("902SGK-004"), "902SGK-004") == 1
    assert select_number_index(index.candidates("SGK-004"), "SGK-004") is None


def test_catalog_merges_shards() -> None:
    index = parse_catalog([_urlset("107STKO-005"), _urlset("107STARS-016")])
    assert index.candidates("STKO-005") == ("107STKO-005",)
    assert index.candidates("STARS-016") == ("107STARS-016",)


def test_catalog_accepts_gzip_shard() -> None:
    """目录端点按 application/x-gzip 原样下发."""
    assert parse_catalog([gzip.compress(_urlset("107STKO-005"))]).candidates("STKO-005") == ("107STKO-005",)


@pytest.mark.parametrize(
    ("payloads", "message"),
    [
        # 不是 sitemap (拦截页 / 首页) 或分片损坏: 这一档不可用, 调用方退到直连与检索.
        ([b"<html><body>maintenance</body></html>"], "no <loc>"),
        ([b"\x1f\x8bnot a gzip stream"], "not valid gzip"),
        ([], "no sitemap shard"),
    ],
)
def test_catalog_rejects_unusable(payloads: list[bytes], message: str) -> None:
    with pytest.raises(CatalogUnusable, match=message):
        parse_catalog(payloads)


def test_catalog_without_leading_digits_is_empty_not_unusable() -> None:
    """目录全是裸番号时照常建索引, 不是失败: 那是「目录里没有带数字段的番号」."""
    index = parse_catalog([_urlset("ABF-355", "SIRO-3759")])
    assert index.candidates("ABF-355") == ("ABF-355",)
    assert index.candidates("STKO-005") == ()


def test_catalog_does_not_let_bare_number_be_taken_by_shared_tail() -> None:
    """裸番号与同尾号的带数字段条目在站点上是两部作品, 裸番号须命中的是自己."""
    # 真实目录里 ``CHA-010`` 与 ``031CHA-010`` 是两个不同的详情页.
    index = parse_catalog([_urlset("CHA-010", "031CHA-010", "JNT-001", "390JNT-001")])
    assert index.candidates("CHA-010") == ("CHA-010", "031CHA-010")
    assert select_number_index(index.candidates("CHA-010"), "CHA-010") == 0
    assert select_number_index(index.candidates("JNT-001"), "JNT-001") == 0
    # 站点的裸番号条目确实不存在时, 只写了数字段的入参仍由同尾号那条接住.
    assert index.candidates("031CHA-010") == ("CHA-010", "031CHA-010")
    assert select_number_index(index.candidates("031CHA-010"), "031CHA-010") == 1


def _index_down() -> RequestError:
    return RequestError(
        f"https://www.mgstage.com/{_SITEMAP_SHARDS[0]}", RequestFailure(kind=FailureKind.CURL, message="boom")
    )


class _FakeWeb:
    """假 WebClient: 目录正文 + 请求计数.

    ``driven.build_mock`` 按夹具文件装配且每条路由恒定应答, 判不了「有没有再请求一次」, 因此这里自己数.
    计数前先 ``sleep(0)`` 让出控制权: 不让出的话所有并发调用都撞在同一个 await 之前, 计数恒为 1,
    即使去掉 ``_index_lock`` 也照绿, 测试就验不出任何东西.
    """

    def __init__(self, shard: bytes | Exception) -> None:
        self._shard = shard
        self.sitemap_calls = 0

    async def get_bytes(self, url: str, **kwargs: object) -> bytes:
        self.sitemap_calls += 1
        await asyncio.sleep(0)
        if isinstance(self._shard, Exception):
            raise self._shard
        return self._shard


def _crawler(web: _FakeWeb) -> MGStageCrawler:
    return MGStageCrawler(HttpClient(web=cast("WebClient", web), browser=None))


@pytest.mark.asyncio
async def test_index_backoff_skips_refetch_until_due() -> None:
    """拉取失败后进入退避: 退避期内不再请求目录端点, 避免每次刮削各付一次超时."""
    web = _FakeWeb(_index_down())
    crawler = _crawler(web)
    await asyncio.gather(*(crawler._from_index("STKO-005") for _ in range(3)))
    # 首个分片就失败即不再试余下两份, 三个并发检索只换来这一轮拉取.
    assert web.sitemap_calls == 1

    # 退避期内再来一次同样不请求: 这条才是「退避」与「并发只拉一次」的区别所在.
    await crawler._from_index("STKO-005")
    assert web.sitemap_calls == 1


@pytest.mark.asyncio
async def test_stale_index_is_kept_when_refresh_fails() -> None:
    """刷新失败沿用旧索引, 不清空: 目录端点挂了不能让已建立的检索能力一起失效."""
    crawler = _crawler(_FakeWeb(_index_down()))
    crawler._index = parse_catalog([_urlset("107STKO-005")])
    crawler._index_at = time.time() - 2 * 86_400  # 已过期, 检索会触发刷新

    result = await crawler._from_index("STKO-005")
    assert result.hit == "https://www.mgstage.com/product/product_detail/107STKO-005/"


@pytest.mark.asyncio
async def test_concurrent_searches_fetch_the_index_once() -> None:
    """并发检索只让一个协程拉取索引."""
    web = _FakeWeb(_urlset("107STKO-005"))
    crawler = _crawler(web)
    await asyncio.gather(*(crawler._from_index("STKO-005") for _ in range(5)))
    assert web.sitemap_calls == len(_SITEMAP_SHARDS)


@pytest.mark.asyncio
async def test_index_rejects_different_leading_digits() -> None:
    """数字段不同即不是同一部: 200MIUM-605 不得命中站点上的 300MIUM-605."""
    crawler = _crawler(_FakeWeb(_urlset("300MIUM-605")))
    assert (await crawler._from_index("200MIUM-605")).hit is None
    assert (await crawler._from_index("300MIUM-605")).hit is not None


@pytest.mark.asyncio
async def test_index_picks_exact_entry_among_shared_tail() -> None:
    """同尾号两部时按入参精确取舍; 入参没写数字段才判无法确认."""
    crawler = _crawler(_FakeWeb(_urlset("483SGK-004", "902SGK-004")))
    assert (await crawler._from_index("483SGK-004")).hit == (
        "https://www.mgstage.com/product/product_detail/483SGK-004/"
    )
    assert (await crawler._from_index("902SGK-004")).hit == (
        "https://www.mgstage.com/product/product_detail/902SGK-004/"
    )
    assert (await crawler._from_index("SGK-004")).ambiguous


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # 站点返回播放列表形态, 播放器脚本把它改写成 mp4; 改写里丢掉整段 query.
        (
            "https://sample.mgstage.com/sample/prestige/abf/355/abf-355_20260508T191004.ism/request"
            "?uid=10000000-0000-0000-0000-00000000000a&amp;pid=c5cc7c58",
            "https://sample.mgstage.com/sample/prestige/abf/355/abf-355_20260508T191004.mp4",
        ),
        # 已经给 mp4 的条目原样保留, 只反转义.
        ("https://sample.mgstage.com/sample/shirouto/siro/3759/SIRO-3759_sample.mp4", None),
        # 早期条目在 dl 子域, 播放器脚本换成 chdl.
        ("http://dl.mgstage.com/sample/a.ism/request?x=1", "http://chdl.mgstage.com/sample/a.mp4"),
        # 没有播放列表后缀时只做反转义, 不改写.
        ("https://sample.mgstage.com/a.mp4?b=1&amp;c=2", "https://sample.mgstage.com/a.mp4?b=1&c=2"),
    ],
)
def test_sample_mp4(url: str, expected: str | None) -> None:
    assert _sample_mp4(url) == (url if expected is None else expected)


def _page(href: str | None) -> str:
    if href is None:
        return "<html><body></body></html>"
    return f'<html><body><p class="sample_movie_btn"><a href="{href}">样品</a></p></body></html>'


def _crawler(web: AsyncMock) -> MGStageCrawler:
    return MGStageCrawler(client=HttpClient(web=web))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "href", "payload", "expected"),
    [
        (
            "播放列表形态改写成 mp4",
            f"/sampleplayer/sampleplayer.html/{_PID}",
            {"url": "https://sample.mgstage.com/s/a.ism/request?uid=1&amp;pid=2"},
            ["https://sample.mgstage.com/s/a.mp4"],
        ),
        (
            "直接给 mp4 原样返回",
            f"/sampleplayer/sampleplayer.html/{_PID}",
            {"url": "https://s/a.mp4"},
            ["https://s/a.mp4"],
        ),
        # 无样品按钮的老条目: 不发请求, 不按命名规则构造地址.
        ("页面没有样品按钮", None, {"url": "https://s/a.mp4"}, []),
        ("播放器返回空 url", f"/sampleplayer/sampleplayer.html/{_PID}", {"url": ""}, []),
        ("播放器返回缺 url", f"/sampleplayer/sampleplayer.html/{_PID}", {}, []),
        ("播放器返回非对象", f"/sampleplayer/sampleplayer.html/{_PID}", ["https://s/a.mp4"], []),
        # 附加资源取不到不能让整条刮削失败.
        (
            "播放器请求失败",
            f"/sampleplayer/sampleplayer.html/{_PID}",
            RequestError(
                "https://www.mgstage.com/sampleplayer/sampleRespons.php",
                RequestFailure(kind=FailureKind.HTTP_STATUS, status=500, message="HTTP 500"),
            ),
            [],
        ),
    ],
)
async def test_trailer_urls(label: str, href: str | None, payload: object, expected: list[str]) -> None:
    web = AsyncMock()
    if isinstance(payload, Exception):
        web.get_json.side_effect = payload
    else:
        web.get_json.return_value = payload
    crawler = _crawler(web)
    assert await crawler._trailer_urls(Selector(text=_page(href))) == expected, label


@pytest.mark.asyncio
async def test_trailer_request_uses_pid() -> None:
    web = AsyncMock()
    web.get_json.return_value = {"url": "https://s/a.mp4"}
    crawler = _crawler(web)

    await crawler._trailer_urls(Selector(text=_page(f"/sampleplayer/sampleplayer.html/{_PID}")))

    web.get_json.assert_awaited_once()
    assert web.get_json.await_args.args[0].endswith(f"?pid={_PID}")


@pytest.mark.asyncio
async def test_trailer_skips_request_without_pid() -> None:
    web = AsyncMock()
    crawler = _crawler(web)

    # 无按钮, 或按钮地址里没有 pid 段: 都不应发出请求.
    await crawler._trailer_urls(Selector(text=_page(None)))
    await crawler._trailer_urls(Selector(text=_page("/sampleplayer/sampleplayer.html/")))

    web.get_json.assert_not_awaited()
