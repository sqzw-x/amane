import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class JavLibraryCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAVLIBRARY, base_url="https://www.javlibrary.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        url = f"{self.base_url}/cn/vl_searchbyid.php?keyword={number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)

        links = html.xpath('//div[@class="video"]//a/@href').getall()
        results = []
        for href in links:
            full_url = urljoin(self.base_url, href)
            results.append(full_url)

        # Filter by number match in title
        titles = html.xpath('//div[@class="video"]//a/@title').getall()
        for i, title in enumerate(titles):
            if number.upper() in title.upper() and i < len(results):
                return results[i]

        return results[0] if results else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        number = extract_text(html, '//div[@id="video_id"]//td[@class="text"]/text()')
        if not number:
            return None

        title = extract_text(html, '//h3[@class="post-title"]/a/text()')
        release = extract_text(html, '//div[@id="video_date"]//td[@class="text"]/text()')
        runtime_str = extract_text(html, '//div[@id="video_length"]//td/span[@class="text"]/text()')
        runtime = self._parse_runtime(runtime_str)
        director = extract_text(html, '//div[@id="video_director"]//td[@class="text"]/a/text()')
        studio = extract_text(html, '//div[@id="video_maker"]//td[@class="text"]/a/text()')
        publisher = extract_text(html, '//div[@id="video_label"]//td[@class="text"]/a/text()')
        actors = extract_all_texts(html, '//span[@class="cast"]/span[@class="star"]/a/text()')
        tags = extract_all_texts(html, '//div[@id="video_genres"]//td[@class="text"]/span[@class="genre"]/a/text()')
        cover = extract_text(html, '//img[@id="video_jacket_img"]/@src')
        score = self._parse_score(extract_text(html, '//span[@class="score"]/text()'))

        directors = [director] if director else []

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            directors=directors,
            thumb_urls=[cover] if cover else [],
            score=score,
            source_url=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_score(text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"\(([\d.]+)\)", text)
        return float(match.group(1)) if match else None
