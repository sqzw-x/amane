"""mgstage 爬虫单元测试: 海报地址推导、样品片地址解析与守卫."""

from unittest.mock import AsyncMock

import pytest
from parsel import Selector

from amane.crawlers.http import HttpClient
from amane.crawlers.sites.mgstage import MGStageCrawler, _poster_url, _sample_mp4
from amane.net.errors import FailureKind, RequestError, RequestFailure

_PID = "c5cc7c58-f209-4fab-b18b-53c9173e0292"


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
