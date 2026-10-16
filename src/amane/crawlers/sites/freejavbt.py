import re
from urllib.parse import unquote, urljoin

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, is_same_number, number_variants

#: 详情页 ``h1`` 的尾注, 各语言不同, 不属于标题.
_TITLE_TAIL = re.compile(r"\s*(?:免費AV在線看|免费AV在线看|Watch for free|Смотреть онлайн)\s*$", re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LEADING_DIGITS = re.compile(r"^\d+")


class FreejavbtCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.FREEJAVBT, base_url="https://freejavbt.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """条目链接末段即番号, 与入参不同者视为未命中.

        站内条目地址只写不带 ``PPV`` 的写法, 因此按 ``number_variants`` 逐个形态尝试.
        """
        for number in number_variants(query.number):
            url = await self._search_number(number, query.number)
            if url is not None:
                return url
        return None

    async def _search_number(self, number: str, query_number: str) -> str | None:
        url = f"{self.base_url}/search/{number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        for item in html.xpath('//div[contains(@class,"category-page") and contains(@class,"video-list-item")]'):
            href = item.xpath(".//a/@href").get()
            if href and is_same_number(_number_from_url(href), query_number):
                return urljoin(self.base_url, href)
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        """字段都在信息行里, 一行一项, 行内标签随语言而变.

        行按 class 与值的形态辨认: 番号 / 导演 / 系列 / 类别 / 女优行带链接, 无链接的行只有日期与时长,
        因此 ``/zh`` / ``/en`` / ``/ru`` 各语言前缀下的页面共用同一套选择器. 站内没有片商与发行商栏.
        """
        text = await self.client.get_html(url)
        html = Selector(text=text)

        number = _number_from_url(url)
        title = _title(html, number)
        if not title:
            return None

        release, runtime = _release_and_runtime(html)
        cover = extract_text(html, '//meta[@property="og:image"]/@content')

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(extract_all_texts(html, '//a[contains(@class,"actress")]/text()')),
            directors=extract_all_texts(html, '//div[contains(@class,"director")]//a/text()'),
            series=extract_text(html, '//div[contains(@class,"series")]//a/text()') or None,
            release=release,
            runtime=runtime,
            tags=extract_all_texts(
                html, '//div[contains(@class,"single-video-meta")]//a[contains(@class,"btn-danger")]/text()'
            ),
            thumb_urls=[cover] if cover else [],
            extrafanart=extract_all_texts(
                html, '//div[contains(@class,"preview")]//a[contains(@class,"tile-item")]/@href'
            ),
            source_url=url,
        )


def _title(html: Selector, number: str) -> str:
    """``h1`` 写作「番号 + 标题 + 尾注」; 番号与尾注都不属于标题."""
    text = re.sub(r"\s+", " ", _TITLE_TAIL.sub("", extract_text(html, "string(//h1)"))).strip()
    if number and text.casefold().startswith(number.casefold()):
        text = text[len(number) :].strip()
    return text


def _release_and_runtime(html: Selector) -> tuple[str | None, int | None]:
    """无链接的信息行里, 日期形的值是发布日期, 其余的前导整数视为片长 (分钟)."""
    release: str | None = None
    runtime: int | None = None
    rows = html.xpath('//div[contains(@class,"single-video-meta")][not(.//a)]')
    for row in rows:
        value = _row_value(row)
        if _DATE.fullmatch(value):
            release = release or value
        elif (match := _LEADING_DIGITS.match(value)) is not None:
            runtime = runtime or int(match.group())
    return release, runtime


def _row_value(row: Selector) -> str:
    """信息行的值: 去掉首个冒号前的标签 (标签随语言而变, 冒号不变)."""
    text = re.sub(r"\s+", " ", " ".join(row.xpath(".//text()").getall())).strip()
    return text.split(":", 1)[1].strip() if ":" in text else text


def _number_from_url(url: str) -> str:
    """取链接末段作为番号; 广告外链的末段不是番号, 比对自然不中."""
    path = url.split("?", 1)[0].rstrip("/")
    return unquote(path.rsplit("/", 1)[-1]) if path else ""
