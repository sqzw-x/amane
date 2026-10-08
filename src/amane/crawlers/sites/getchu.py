from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from ..parsing import extract_all_texts, extract_text, leading_token


class GetchuCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.GETCHU,
            base_url="http://www.getchu.com",
            cookies={"getchu_adalt_flag": "getchu.com", "gc": "gc"},
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number
        search_url = f"{self.base_url}/php/nsearch.phtml?search_keyword={number}&gc=gc"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)
        results = html.xpath('//a[contains(@href,"soft.phtml")]/@href').getall()
        urls = [urljoin(self.base_url, r) for r in results]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//h1[@id="soft-title"]/text()')
        if not title:
            return None

        cover = extract_text(html, '//img[@class="product_img"]/@src')
        if cover and not cover.startswith("http"):
            cover = urljoin(self.base_url, cover)

        release = extract_text(html, '//td[contains(text(),"発売日")]/following-sibling::td/text()')
        studio = extract_text(html, '//td[contains(text(),"ブランド")]/following-sibling::td/a/text()')
        tags = extract_all_texts(html, '//a[contains(@href,"genre")]/text()')

        # Extract number from title
        number = leading_token(title)

        return MediaMetadata(
            number=number,
            title=title,
            studio=studio or None,
            release=release or None,
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )
