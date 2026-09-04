import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class FC2PPVDBCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FC2PPVDB, base_url="https://fc2ppvdb.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        # Extract numeric part
        num = re.sub(r"(?i)fc2[-_]?(?:ppv[-_]?)?", "", number).strip()
        url = f"{self.base_url}/articles/{num}"
        text = await self.client.get_html(url)
        if text and "title" in text.lower():
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//h1[@class="title"]/text()', '//h2[@class="title"]/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//a[contains(@href,"/actresses/")]/text()')
        cover = extract_text(html, '//div[@class="article-image"]//img/@src')
        tags = extract_all_texts(html, '//a[contains(@href,"/tags/")]/text()')

        # Try datetime attribute first, then text content
        release = extract_text(html, "//time/@datetime")
        if not release:
            time_text = extract_text(html, "//time/text()")
            if time_text:
                date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", time_text)
                release = date_match.group(1) if date_match else None

        # Extract number from URL
        num_match = re.search(r"/articles/(\d+)", url)
        number = f"FC2-{num_match.group(1)}" if num_match else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            release=release,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )
