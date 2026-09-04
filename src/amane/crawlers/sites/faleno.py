import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class FalenoCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FALENO, base_url="https://faleno.jp")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        search_url = f"{self.base_url}/top/search?keyword={number}"
        text = await self.client.get_html(search_url)
        if not text:
            return None

        html = Selector(text=text)
        results = html.xpath('//a[contains(@href,"/products/detail/")]/@href').getall()
        urls = [urljoin(self.base_url, r) for r in results]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//h2[@class="p-product-title"]/text()', "//h1/text()")
        if not title:
            return None

        actors = extract_all_texts(html, '//a[contains(@href,"/actress/")]/text()')
        cover = extract_text(html, '//div[contains(@class,"product-image")]//img/@src')
        release = extract_text(html, '//dt[text()="発売日"]/following-sibling::dd/text()')
        runtime_raw = extract_text(html, '//dt[text()="収録時間"]/following-sibling::dd/text()')
        runtime = self._parse_runtime(runtime_raw)
        tags = extract_all_texts(html, '//a[contains(@href,"/genre/")]/text()')
        series = extract_text(html, '//a[contains(@href,"/series/")]/text()')
        extrafanart = extract_all_texts(html, '//div[contains(@class,"swiper-slide")]//img/@src')

        # Extract number from title
        number = title.split()[0] if title else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            studio="FALENO",
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            thumb_urls=[cover] if cover else [],
            extrafanart=extrafanart,
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
