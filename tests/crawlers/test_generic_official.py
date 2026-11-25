"""通用官网源单元测试: 标准位置提取、空页面守卫与多链接回退."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.aggregate.models import AggregatedMetadata
from amane.crawlers.http import HttpClient
from amane.crawlers.sites.generic_official import GenericOfficialCrawler
from amane.net.errors import FailureKind, RequestError, RequestFailure

URL = "https://example-studio.com/scene/melody-marks-2/"


def _crawler(route: Callable[[str], str]) -> GenericOfficialCrawler:
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        return route(url)

    web.get_text.side_effect = get_text
    return GenericOfficialCrawler(client=HttpClient(web=web))


def _query(urls: list[str]) -> object:
    partial = AggregatedMetadata(number="dreddxxx.melody.maks.xmas", title="Melody Marks #2", homepage_urls=urls)
    from amane.crawlers.models import SearchQuery

    return SearchQuery(number="dreddxxx.melody.maks.xmas", partial_result=partial)


def _ld_page(ld: dict) -> str:
    import json

    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body></body></html>'


@pytest.mark.asyncio
async def test_json_ld_first() -> None:
    """JSON-LD 最可靠: 标题、描述、日期、封面、预告片都从它取."""
    page = _ld_page(
        {
            "@type": "VideoObject",
            "name": "Melody Marks #2",
            "description": "This scene features Melody Marks and Dredd.",
            "uploadDate": "2025-04-16T19:34:24+00:00",
            "thumbnailUrl": "https://example-studio.com/cover.jpg",
            "contentUrl": "https://example-studio.com/trailer.mp4",
        }
    )
    meta = await _crawler(lambda url: page).fetch(_query([URL]))

    assert meta is not None
    assert meta.title == "Melody Marks #2"
    assert meta.release == "2025-04-16"
    assert meta.thumb_urls == ["https://example-studio.com/cover.jpg"]
    assert meta.trailer_urls == ["https://example-studio.com/trailer.mp4"]


@pytest.mark.asyncio
async def test_og_meta_fallback() -> None:
    """没有 JSON-LD 时退回 og:meta."""
    page = """<html><head>
      <title>Melody Marks #2</title>
      <meta property="og:title" content="Melody Marks #2 - Official">
      <meta property="og:image" content="https://example-studio.com/og.jpg">
      <meta property="article:published_time" content="2025-04-16T19:34:24+00:00">
    </head><body></body></html>"""
    meta = await _crawler(lambda url: page).fetch(_query([URL]))

    assert meta is not None
    assert meta.title == "Melody Marks #2 - Official"
    assert meta.release == "2025-04-16"
    assert meta.thumb_urls == ["https://example-studio.com/og.jpg"]


@pytest.mark.asyncio
async def test_teaser_from_wordpress_path() -> None:
    """WordPress 站点的预告片在 uploads 下, 文件名带 teaser."""
    page = """<html><head><title>x</title></head><body>
      <video src="https://example-studio.com/wp-content/uploads/2025/04/melody-marks-2-teaser.mp4"></video>
    </body></html>"""
    meta = await _crawler(lambda url: page).fetch(_query([URL]))

    assert meta is not None
    assert meta.trailer_urls == ["https://example-studio.com/wp-content/uploads/2025/04/melody-marks-2-teaser.mp4"]


@pytest.mark.asyncio
async def test_actors_from_description_when_missing() -> None:
    """JSON-LD 没有 actor 时, 从描述里的 "features X and Y" 取; 描述里的多个人都要."""
    page = _ld_page(
        {
            "@type": "VideoObject",
            "name": "x",
            "description": "This scene features Liz Jordan and Dredd in a huge scene.",
        }
    )
    meta = await _crawler(lambda url: page).fetch(_query([URL]))

    assert meta is not None
    assert [a.name for a in meta.actors] == ["Liz Jordan", "Dredd"]


@pytest.mark.asyncio
async def test_empty_page_is_miss() -> None:
    """一条可用字段都没有的页面不算命中, 别把空壳写进库."""
    page = "<html><head><title></title></head><body></body></html>"
    assert await _crawler(lambda url: page).fetch(_query([URL])) is None


@pytest.mark.asyncio
async def test_no_homepage_urls_is_miss() -> None:
    """上游没给官网链接时不发请求."""
    web = AsyncMock()
    crawler = GenericOfficialCrawler(client=HttpClient(web=web))
    assert await crawler.fetch(_query([])) is None
    web.get_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_through_dead_links() -> None:
    """前一个链接 404 就换下一个, 不是整次失败."""
    pages = {"https://a.com/x/": "", "https://b.com/x/": _ld_page({"name": "ok"})}

    def route(url: str) -> str:
        if url == "https://a.com/x/":
            raise RequestError(url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="404"))
        return pages[url]

    meta = await _crawler(route).fetch(_query(["https://a.com/x/", "https://b.com/x/"]))

    assert meta is not None
    assert meta.title == "ok"
