from urllib.parse import quote, urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, is_same_number, leading_token


class AiravCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.AIRAV, base_url="https://airav.io")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """条目标题以番号开头, 与入参不同者视为未命中."""
        number = query.number
        url = f"{self.base_url}/search_result?kw={quote(number)}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        for item in html.xpath('//div[contains(@class,"oneVideo")]'):
            href = item.xpath(".//a/@href").get()
            title = item.xpath("string(.//h5)").get() or ""
            if href and is_same_number(leading_token(title), number):
                return urljoin(self.base_url, href)
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        """详情页的番号与标题同在一个 h1 里, 其余字段都在信息表的条目中; 页面只提供固定的试看时长, 因此不解析片长."""
        text = await self.client.get_html(url)
        html = Selector(text=text)

        number = extract_text(html, '//li[contains(text(), "番")]/span/text()')
        heading = extract_text(html, '//div[contains(@class, "video-title")]//h1/text()')
        if not number or not heading:
            return None

        token = leading_token(heading)
        title = heading[len(token) :].strip() if is_same_number(token, number) else heading

        cover = extract_text(html, '//meta[@property="og:image"]/@content')
        if cover and not cover.startswith("http"):
            cover = urljoin(self.base_url, cover)

        actors = extract_all_texts(html, '//a[contains(@href, "actor?id=")]/text()')
        studio = extract_text(html, '//a[contains(@href, "tag?fid=")]/text()')
        release = extract_text(
            html, '//div[contains(@class, "video-item")]//i[contains(@class, "fa-clock")]/parent::*/text()'
        )
        tags = extract_all_texts(html, '//a[contains(@href, "tag?tid=")]/text()')
        plot = extract_text(html, '//div[contains(@class, "video-info")]/p/text()')

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            release=release or None,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            plot=plot or None,
            source_url=url,
        )
