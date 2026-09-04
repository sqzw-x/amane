import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class GigaCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.GIGA, base_url="https://www.giga-web.jp")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        search_url = f"{self.base_url}/top/search?keyword={number}"
        text = await self.client.get_html(search_url)
        if not text:
            return None

        html = Selector(text=text)
        results = html.xpath('//div[@class="item"]//a/@href').getall()
        urls = [urljoin(self.base_url, r) for r in results]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//h1[@class="title"]/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//a[contains(@href,"actress")]/text()')
        cover = extract_text(html, '//img[@id="jacket-img"]/@src')
        release = extract_text(html, '//dd[dt[text()="発売日"]]/text()')
        runtime_raw = extract_text(html, '//dd[dt[text()="収録時間"]]/text()')
        runtime = self._parse_runtime(runtime_raw)
        tags = extract_all_texts(html, '//a[contains(@href,"genre")]/text()')
        extrafanart = extract_all_texts(html, '//ul[@class="sample-image-list"]//img/@src')
        extrafanart = [urljoin(url, u) for u in extrafanart]

        number = title.split()[0] if title else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            studio="GIGA",
            release=release or None,
            runtime=runtime,
            tags=tags,
            thumb_urls=[urljoin(url, cover)] if cover else [],
            extrafanart=extrafanart,
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
