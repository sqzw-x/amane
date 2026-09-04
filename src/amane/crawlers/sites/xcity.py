import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class XCityCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.XCITY, base_url="https://xcity.jp", cookies={"adc": "1"})

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        search_term = number.replace("-", "")
        search_url = f"{self.base_url}/result_published/?q={search_term}"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)
        results = html.xpath('//table[@class="resultList"]//a[contains(@href,"/titles/")]/@href').getall()
        urls = [f"{self.base_url}{r}" if r.startswith("/") else r for r in results]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, "//h1/text()")
        if not title:
            return None

        number = extract_text(html, '//span[@class="kopin"]/text()')
        actors = extract_all_texts(html, '//a[contains(@href,"/idol/")]/text()')
        cover = extract_text(html, '//img[@class="photo"]/@src', '//a[@class="fancy_image"]/@href')
        release_raw = extract_text(html, '//li[span[text()="発売日"]]/text()')
        runtime_raw = extract_text(html, '//li[span[text()="収録時間"]]/text()')
        runtime = self._parse_runtime(runtime_raw)
        studio = extract_text(html, '//a[contains(@href,"/studios/")]/text()')
        tags = extract_all_texts(html, '//a[contains(@href,"/genres/")]/text()')

        return MediaMetadata(
            number=number or title,
            title=title,
            actors=film_actors(actors),
            studio=studio or None,
            release=release_raw or None,
            runtime=runtime,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
