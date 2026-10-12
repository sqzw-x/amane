"""官网番号形如 ``KIN8-0001`` / ``KIN8TENGOKU-0001``, 详情页 id 即其 4 位数字段."""

from __future__ import annotations

import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text

# 只认本站前缀, 不认「任意番号里的首个数字串」: heyzo-1234 与 010115_001 都含数字, 拼出的是别人的 id.
_NUMBER_PREFIX = re.compile(r"^KIN8(?:TENGOKU)?[-_ ]?(?P<id>\d+)$")


class Kin8Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.KIN8, base_url="https://www.kin8tengoku.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        prefix_match = _NUMBER_PREFIX.match(query.number.strip().upper())
        if prefix_match is None:
            return None
        url = f"{self.base_url}/moviepages/{prefix_match['id']}/index.html"
        text = await self.client.get_html(url)
        if text and "title" in text.lower():
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//div[@id="title"]/h1/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//div[@id="actress"]//a/text()')
        cover = extract_text(html, '//div[@id="gallery"]//img/@src')

        release_raw = extract_text(html, '//div[@id="detail"]//li[contains(text(),"配信日")]/text()')
        release = None
        if release_raw:
            date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", release_raw)
            release = date_match.group(1) if date_match else release_raw

        tags = extract_all_texts(html, '//div[@id="tag"]//a/text()')

        # Extract ID from URL
        id_match = re.search(r"/moviepages/(\d+)/", url)
        number = id_match.group(1) if id_match else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            release=release,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )
