"""data18 爬虫单元测试: 检索词裁剪、场景 key 提取与演员确认."""

from unittest.mock import AsyncMock

import pytest

from amane.aggregate.models import AggregatedMetadata
from amane.crawlers.http import HttpClient
from amane.crawlers.models import FilmActor, SearchQuery
from amane.crawlers.sites.data18 import Data18Crawler, search_key

MOVIE_PAGE = """<html><head><title>Movie</title></head><body>
<a href="https://www.data18.com/scenes/3120555">a</a>
<a href="https://www.data18.com/scenes/3120556">b</a>
</body></html>"""


def _scene_page(actress: str, photoset: int = 15) -> str:
    return f"""<html><head><title>{actress} in Omg! I Fucked My Bratty Student 2 | DATA18</title></head>
<body>
<img src="https://cdn.dt18.com/media/scenes/3/5/120556-melody-marks-omg-i-fucked.jpg">
<span>Photoset <b>{photoset}</b></span>
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
