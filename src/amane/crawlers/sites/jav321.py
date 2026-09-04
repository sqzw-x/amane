import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class Jav321Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAV321, base_url="https://www.jav321.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        url = f"{self.base_url}/video/{number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        title = html.xpath('//div[@class="panel-heading"]/h3/text()').get()
        if title and title.strip():
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        title = extract_text(html, '//div[@class="panel-heading"]/h3/text()')
        if not title:
            return None

        # Extract number from title, falling back to URL
        number_match = re.search(r"([A-Za-z]+-\d+)", title)
        if not number_match:
            number_match = re.search(r"/video/(.+?)$", url)
        number = number_match.group(1) if number_match else title.strip()

        actors = extract_all_texts(html, '//b[contains(text(),"出演者")]/following-sibling::a/text()')
        studio = extract_text(html, '//b[contains(text(),"メーカー")]/following-sibling::a/text()')
        release = extract_text(html, '//b[contains(text(),"配信開始日")]/following-sibling::text()')
        runtime_str = extract_text(html, '//b[contains(text(),"収録時間")]/following-sibling::text()')
        runtime = self._parse_runtime(runtime_str)
        tags = extract_all_texts(html, '//b[contains(text(),"ジャンル")]/following-sibling::a/text()')
        cover = html.xpath('//div[@class="panel-body"]//img/@src').get()
        plot = extract_text(html, '//div[@class="panel-body"]/div[@class="row"]/div[@class="col-md-12"]/text()')

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            plot=plot or None,
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
