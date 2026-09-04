import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class FreejavbtCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FREEJAVBT, base_url="https://freejavbt.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        url = f"{self.base_url}/search/{number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        links = html.xpath('//article[contains(@class,"post")]//a/@href').getall()
        urls = list(dict.fromkeys(links))[:5]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        title = extract_text(html, '//h1[@class="entry-title"]/text()')
        if not title:
            return None

        # Extract number from title (usually first part like "ABC-123")
        number_match = re.search(r"([A-Za-z]+-\d+)", title)
        number = number_match.group(1) if number_match else title

        actors = extract_all_texts(html, '//strong[contains(text(),"出演者")]/following-sibling::a/text()')
        studio = extract_text(html, '//strong[contains(text(),"メーカー")]/following-sibling::a/text()')
        publisher = extract_text(html, '//strong[contains(text(),"レーベル")]/following-sibling::a/text()')
        series = extract_text(html, '//strong[contains(text(),"シリーズ")]/following-sibling::a/text()')
        release = extract_text(
            html, '//strong[contains(text(),"配信日") or contains(text(),"発売日")]/following-sibling::text()'
        )
        runtime_str = extract_text(html, '//strong[contains(text(),"収録時間")]/following-sibling::text()')
        runtime = self._parse_runtime(runtime_str)
        tags = extract_all_texts(html, '//strong[contains(text(),"ジャンル")]/following-sibling::a/text()')
        cover = extract_text(html, '//article//img[contains(@class,"alignnone")]/@src')
        extrafanart = extract_all_texts(html, '//article//a[contains(@href,".jpg")]/@href')

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            series=series or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
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
