import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class AiravCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.AIRAV, base_url="https://airav.io")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        url = f"{self.base_url}/search?keyword={number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        links = html.xpath('//div[@class="oneVideo"]//a/@href').getall()
        urls = [urljoin(self.base_url, href) for href in links][:5]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        number = extract_text(html, '//span[@class="video_code"]/text()')
        if not number:
            return None

        title = extract_text(html, '//h5[@class="video_title"]/text()')
        actors = extract_all_texts(html, '//li[span[contains(text(),"演員")]]/a/text()')
        studio = extract_text(html, '//li[span[contains(text(),"廠商")]]/a/text()')
        release = extract_text(html, '//li[span[contains(text(),"日期")]]/text()[last()]')
        runtime_str = extract_text(html, '//li[span[contains(text(),"時長")]]/text()')
        runtime = self._parse_runtime(runtime_str)
        tags = extract_all_texts(html, '//div[@class="tagBtnMargin"]/a/text()')
        cover = extract_text(html, '//img[@id="video_jacket_img"]/@src')
        plot = extract_text(html, '//div[@class="video_description"]/text()')

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
