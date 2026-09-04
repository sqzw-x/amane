import re
from urllib.parse import urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text


class JavBusCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.JAVBUS,
            base_url="https://www.javbus.com",
            cookies={"dv": "1"},
            headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"},
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number

        # 先尝试直接访问详情页.
        direct_url = f"{self.base_url}/{number}"
        try:
            text = await self.client.get_html(direct_url, cookies=self.cookies, headers=self.headers)
        except RequestError:
            text = None
        if text and "movie-box" in text:
            html = Selector(text=text)
            title = extract_text(html, "//h3/text()")
            if title:
                return direct_url

        # 未命中则回退搜索.
        search_url = f"{self.base_url}/search/{number}&type=&parent=ce"
        text = await self.client.get_html(search_url, cookies=self.cookies, headers=self.headers)
        html = Selector(text=text)
        results = html.xpath("//a[@class='movie-box']/@href").getall()
        urls = [urljoin(self.base_url, r) for r in results]
        return urls[0] if urls else None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies, headers=self.headers)
        html = Selector(text=text)

        title = extract_text(html, "//h3/text()")
        if not title:
            return None

        number = extract_text(
            html, '//span[@class="header"][contains(text(), "識別碼:")]/following-sibling::span/text()'
        )
        if not number:
            return None

        actors = extract_all_texts(html, '//div[@class="star-name"]/a/text()')

        cover = extract_text(html, '//a[@class="bigImage"]/@href')
        thumb_url = urljoin(url, cover) if cover else None

        # poster: /cover/ -> /thumb/, _b.jpg -> .jpg
        poster_url = None
        if cover:
            poster = cover.replace("/cover/", "/thumb/").replace("_b.jpg", ".jpg")
            poster_url = urljoin(url, poster)

        release_raw = extract_text(html, '//span[@class="header"][contains(text(), "發行日期:")]/parent::p/text()')
        release = release_raw.strip() if release_raw else None

        runtime_raw = extract_text(html, '//span[@class="header"][contains(text(), "長度:")]/parent::p/text()')
        runtime = self._parse_runtime(runtime_raw)

        studio = extract_text(html, '//a[contains(@href, "/studio/")]/text()')
        publisher = extract_text(html, '//a[contains(@href, "/label/")]/text()')
        director = extract_text(html, '//a[contains(@href, "/director/")]/text()')
        directors = [director] if director else []
        series = extract_text(html, '//a[contains(@href, "/series/")]/text()')
        tags = extract_all_texts(html, '//span[@class="genre"]/label/a[contains(@href, "/genre/")]/text()')
        extrafanart = extract_all_texts(html, "//div[@id='sample-waterfall']/a/@href")
        extrafanart = [urljoin(url, u) for u in extrafanart]

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=publisher or None,
            release=release,
            runtime=runtime,
            tags=tags,
            series=series or None,
            directors=directors,
            poster_urls=[poster_url] if poster_url else [],
            thumb_urls=[thumb_url] if thumb_url else [],
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
