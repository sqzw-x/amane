import re

from parsel import Selector

from ...enums import ActorGender, SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class FC2ClubCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FC2CLUB, base_url="https://fc2club.top")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        # Extract numeric part from number like FC2-123456 or just 123456
        num = re.sub(r"(?i)fc2[-_]?(?:ppv[-_]?)?", "", number).strip()
        url = f"{self.base_url}/html/FC2-{num}.html"
        text = await self.client.get_html(url)
        if text and "show-top-grids" in text:
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//div[@class="show-top-grids"]//h3/text()')
        if not title:
            return None

        actors = extract_all_texts(html, '//ul[@class="slides"]//h5[@class="title"]/text()')
        cover = extract_text(html, '//div[@class="show-top-grids"]//img/@src')
        tags = extract_all_texts(html, '//a[contains(@href,"/tag/")]/text()')

        release_raw = extract_text(html, '//p[contains(text(),"日期")]/text()')
        release = None
        if release_raw:
            date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", release_raw)
            release = date_match.group(1) if date_match else None

        # Extract FC2 number from URL
        num_match = re.search(r"FC2-(\d+)", url)
        number = f"FC2-{num_match.group(1)}" if num_match else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors, gender=ActorGender.UNKNOWN),
            release=release,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )
