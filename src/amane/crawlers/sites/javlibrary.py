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

        # 每个结果只有一个带 href 的链接, 其余 "我想要" 之类没有 href;
        # 链接相对检索页的 /cn/ 目录, 必须按检索 URL 解析, 按基址解析会落到 404.
        fallback: str | None = None
        target = number.upper()
        for item in html.xpath('//div[@class="video"]'):
            href = item.xpath(".//a[@href]/@href").get()
            if not href:
                continue
            full_url = urljoin(url, href)
            if fallback is None:
                fallback = full_url
            title = item.xpath(".//a[@href]/@title").get() or ""
            if target in title.upper():
                return full_url

        return fallback

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        number = extract_text(html, '//div[@id="video_id"]//td[@class="text"]/text()')
        if not number:
            return None

        title = extract_text(html, '//h3[contains(@class, "post-title")]/a/text()')
        release = extract_text(html, '//div[@id="video_date"]//td[@class="text"]/text()')
        runtime_str = extract_text(html, '//div[@id="video_length"]//td/span[@class="text"]/text()')
        runtime = self._parse_runtime(runtime_str)
        director = extract_text(html, '//div[@id="video_director"]//td[@class="text"]//a/text()')
        studio = extract_text(html, '//div[@id="video_maker"]//td[@class="text"]//a/text()')
        publisher = extract_text(html, '//div[@id="video_label"]//td[@class="text"]//a/text()')
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
