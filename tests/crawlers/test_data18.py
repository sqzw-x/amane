"""data18 爬虫单元测试: 检索词裁剪、场景 key 提取与演员确认."""

from unittest.mock import AsyncMock

import pytest

from amane.aggregate.models import AggregatedMetadata
from amane.crawlers.http import HttpClient
from amane.crawlers.models import FilmActor, SearchQuery
from amane.crawlers.sites.data18 import _PHOTOSET_TABS, Data18Crawler, search_key
from amane.enums import TrailerMode

MOVIE_PAGE = """<html><head><title>Movie</title></head><body>
<a href="https://www.data18.com/scenes/3120555">a</a>
<a href="https://www.data18.com/scenes/3120556">b</a>
</body></html>"""


def _scene_page(actress: str, photoset: int = 15, *, extra_group: int = 0) -> str:
    """渲染后才会出现的剧照地址; 前缀与文件名形态都因场景而异, 这里用真实形态.

    ``extra_group`` > 0 时额外给一组 Photoset: 一个场景可能有多组, 组间切换互斥.
    """
    groups = (
        f'<div id="gallery1001" class="changeimggallery"><span class="gen bold">Gallery #1</span>'
        f'&nbsp;<span class="gen11">Photoset <b>{photoset}</b></span></div>'
    )
    if extra_group:
        groups += (
            f'<div id="gallery1901" class="changeimggallery"><span class="gen bold">Gallery #2</span>'
            f'&nbsp;<span class="gen11">Photoset <b>{extra_group}</b></span></div>'
        )
    return f"""<html><head><title>{actress} in Omg! I Fucked My Bratty Student 2 | DATA18</title></head>
<body>
{groups}
<img src="https://cdn.dt18.com/media/scenes/3/5/120556-melody-marks-omg-i-fucked.jpg">
<img data-src="https://imb.dt18.com/314/128/120556/t01.jpg">
</body></html>"""


@pytest.mark.parametrize(
    ("label", "title", "expected"),
    [
        # TPDB 标题带场景序号与演员名; 检索只用作品名.
        (
            "带演员与序号",
            "Omg I Fucked My Bratty Student #02 - Melody Marks & Dorian Del Isla",
            "Omg I Fucked My Bratty Student",
        ),
        ("只有作品名", "Some Title", "Some Title"),
        ("序号在尾部", "Title #3", "Title"),
    ],
)
def test_search_key(label: str, title: str, expected: str) -> None:
    assert search_key(title) == expected, label


def _crawler(routes: dict[str, str]) -> Data18Crawler:
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        for prefix, body in routes.items():
            if prefix in url:
                return body
        raise AssertionError(f"未预期的请求: {url}")

    web.get_text.side_effect = get_text
    return Data18Crawler(client=HttpClient(web=web))


def _query(actors: list[str], title: str = "Title #02 - A & B") -> SearchQuery:
    return SearchQuery(
        number="x",
        partial_result=AggregatedMetadata(number="x", title=title, actors=[FilmActor(name=name) for name in actors]),
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (TrailerMode.TRAILER, 1),
        (TrailerMode.PREVIEW, 1),
        (TrailerMode.ALL, 2),
    ],
)
def test_video_kinds_follows_site_config(mode: TrailerMode, expected: int) -> None:
    """站点配置决定抓哪种视频: 只预告片 / 只预览片 / 两者."""
    from amane.config.manager import SiteConfig

    crawler = Data18Crawler(client=HttpClient(web=AsyncMock()), config=SiteConfig(trailer_mode=mode))
    assert len(crawler._video_kinds()) == expected


def _group(gid: str, count: int) -> str:
    return (
        f'<div id="{gid}" class="changeimggallery"><span class="gen bold">Gallery #1</span>'
        f'&nbsp;<span class="gen11">Photoset <b>{count}</b></span></div>'
    )


@pytest.mark.parametrize(
    ("label", "page", "expected"),
    [
        ("单组", _group("gallery1001", 15), [("gallery1001", 15)]),
        # 一个场景可能有多组 Photoset, 组间切换互斥, 必须全部识别出来逐组渲染.
        ("两组", _group("gallery1001", 5) + _group("gallery1901", 15), [("gallery1001", 5), ("gallery1901", 15)]),
        # Video Stills / Primary 不是 Photoset, 不参与.
        (
            "只有 Video Stills",
            '<div id="gallery1101"><span>Gallery #2</span><span>Video Stills <b>37</b></span></div>',
            [],
        ),
    ],
)
def test_photoset_tabs(label: str, page: str, expected: list[tuple[str, int]]) -> None:
    assert [(m["id"], int(m["count"])) for m in _PHOTOSET_TABS.finditer(page)] == expected, label


@pytest.mark.asyncio
async def test_fetch_builds_gallery() -> None:
    crawler = _crawler(
        {
            "live.php": '<a href="https://www.data18.com/movies/1251471-x">m</a>',
            "/movies/": MOVIE_PAGE,
            "/scenes/3120555": _scene_page("Kimmy Kimm"),
            "/scenes/3120556": _scene_page("Melody Marks"),
        }
    )

    meta = await crawler.fetch(_query(["Melody Marks", "Dorian Del Isla"]))

    assert meta is not None
    assert meta.number == "120556"
    assert len(meta.extrafanart) == 15
    assert meta.extrafanart[0] == "https://imb.dt18.com/314/128/120556/t01.jpg"
    assert meta.extrafanart[-1] == "https://imb.dt18.com/314/128/120556/t15.jpg"


@pytest.mark.asyncio
async def test_fetch_without_matching_actress_returns_none() -> None:
    crawler = _crawler(
        {
            "live.php": '<a href="https://www.data18.com/movies/1251471-x">m</a>',
            "/movies/": MOVIE_PAGE,
            "/scenes/3120555": _scene_page("Kimmy Kimm"),
            "/scenes/3120556": _scene_page("Katie Kush"),
        }
    )

    assert await crawler.fetch(_query(["Melody Marks"])) is None


@pytest.mark.asyncio
async def test_fetch_without_partial_returns_none() -> None:
    crawler = _crawler({})

    assert await crawler.fetch(SearchQuery(number="x")) is None


@pytest.mark.asyncio
async def test_fetch_with_zero_photoset_returns_none() -> None:
    crawler = _crawler(
        {
            "live.php": '<a href="https://www.data18.com/movies/1251471-x">m</a>',
            "/movies/": MOVIE_PAGE,
            "/scenes/3120555": _scene_page("Melody Marks", photoset=0),
            "/scenes/3120556": _scene_page("Katie Kush", photoset=0),
        }
    )

    assert await crawler.fetch(_query(["Melody Marks"])) is None
