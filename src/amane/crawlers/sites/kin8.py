import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class Kin8Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.KIN8, base_url="https://www.kin8tengoku.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        match = re.search(r"(\d+)", number)
        if not match:
            return None
        movie_id = match.group(1)
        url = f"{self.base_url}/moviepages/{movie_id}/index.html"
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
        if cover and not cover.startswith("http"):
            cover = urljoin(self.base_url, cover)

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
