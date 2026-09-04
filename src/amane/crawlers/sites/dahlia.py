import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class DahliaCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.DAHLIA, base_url="https://dahlia-av.jp")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        slug = number.lower().replace("-", "")
        url = f"{self.base_url}/works/{slug}/"
        text = await self.client.get_html(url)
        if text and "<title" in text:
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//h1[@class="p-workPage__title"]/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//a[contains(@href,"/actress/")]/text()')
        cover = extract_text(html, '//div[@class="p-workPage__jacket"]//img/@src')
        release = extract_text(html, '//dt[text()="発売日"]/following-sibling::dd/text()')
        runtime_raw = extract_text(html, '//dt[text()="収録時間"]/following-sibling::dd/text()')
        runtime = self._parse_runtime(runtime_raw)
        tags = extract_all_texts(html, '//a[contains(@href,"/genre/")]/text()')
        series = extract_text(html, '//a[contains(@href,"/series/")]/text()')
        extrafanart = extract_all_texts(html, '//div[contains(@class,"swiper-slide")]//img/@src')

        # Extract number from URL slug
        slug = url.rstrip("/").split("/")[-1]

        return MediaMetadata(
            number=slug.upper(),
            title=title,
            actors=film_actors(actors),
            studio="DAHLIA",
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
