"""カリビアンコム官网.

番号形如 ``MMDDYY-NNN``; 详情是静态页 ``/moviepages/<番号>/index.html``, 未知番号 404,
不会把无关番号拼成详情页. 短横线是本站专属形态: 同日期号的一本道作品写下划线, 两者不是同一部,
形态不符时不发请求.

页面是 euc-jp 且正文偶有坏字节, 按替换解码. 站点没有竖版海报: ``images/l_l.jpg`` 是 960×540 横版封面,
因此只填 ``thumb_urls``, 竖版由刮削流程从封面裁切. 预告片取页面声明的 sample URL, 早年条目没有 sample,
不按命名规则构造地址.
"""

from __future__ import annotations

import re
from typing import override

from parsel import Selector

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome
from ...net.errors import FailureReason, RequestError
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text

# 本站形态: 六位日期号 + 短横线 + 三位序号. 下划线形态属于同集团的其它厂牌, 不发请求.
_NUMBER = re.compile(r"\d{6}-\d{3}")
_STUDIO = "カリビアンコム"
# 详情页声明的样品地址, 按序取第一个非空: 完整版样品 → 站内播放器样品 → 移动版样品.
_SAMPLE_URLS = tuple(
    re.compile(rf'"{key}":"(?P<url>[^"]+)"')
    for key in ("sample_aff_flash_url", "sample_flash_url", "sample_m_flash_url")
)
# 页面自报的番号, 用于确认跳转后仍是目标条目. 键名在 bookmarks 等处写作 'movie_id', 值可能带双引号.
_PAGE_NUMBER = re.compile(r"""movie_id['"]?\s*:\s*['"](?P<number>[0-9]{6}-[0-9]{3})['"]""")
# ISO-8601 时长 (PT 省略, 站点写成 T01H21M09S).
_DURATION = re.compile(r"T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?")

_INFO = "//div[contains(@class, 'movie-info')]"


class CaribbeanComCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.CARIBBEANCOM, base_url="https://www.caribbeancom.com")

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        # 只有一次请求: 基类的 _search → _scrape 会对同一页取两次.
        url = self._detail_url(query.number)
        if url is None:
            return None
        return await self._load(url)

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """根路径是年龄门, 影片列表页才反映站点是否可达."""
        return await self.client.check(f"{self.base_url}/index2.htm", cookies=self.cookies, headers=self.headers)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return await self._load(url)

    def _detail_url(self, number: str) -> str | None:
        value = number.strip()
        if _NUMBER.fullmatch(value) is None:
            return None
        return f"{self.base_url}/moviepages/{value}/index.html"

    async def _load(self, url: str) -> MediaMetadata | None:
        number = url.split("/moviepages/", 1)[-1].split("/", 1)[0]
        try:
            text = await self.client.get_html(url, cookies=self.cookies, headers=self.headers, encoding="euc-jp")
        except RequestError as exc:
            # 未知番号是未命中, 其余失败照旧上报.
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        return to_metadata(text, number, url, base_url=self.base_url)


def to_metadata(text: str, number: str, url: str, *, base_url: str) -> MediaMetadata | None:
    html = Selector(text=text)
    title = extract_text(html, f"{_INFO}//h1[@itemprop='name']/text()")
    if not title or not _confirms_number(text, number):
        return None
    return MediaMetadata(
        number=number,
        title=title,
        actors=film_actors(extract_all_texts(html, f"{_INFO}//a[@itemprop='actor']//span[@itemprop='name']/text()")),
        studio=_STUDIO,
        release=_release(html),
        runtime=_minutes(extract_text(html, f"{_INFO}//span[@itemprop='duration']/@content")),
        tags=extract_all_texts(html, f"{_INFO}//a[@class='spec-item']/text()"),
        series=extract_text(html, f"{_INFO}//li[.//span[@class='spec-title'][normalize-space()='シリーズ']]//a/text()")
        or None,
        plot=extract_text(html, f"{_INFO}//p[@itemprop='description']/text()") or None,
        thumb_urls=[f"{base_url}/moviepages/{number}/images/l_l.jpg"],
        trailer_urls=_trailer_urls(text),
        source_url=url,
    )


def _release(html: Selector) -> str | None:
    """配信日只出现在 spec 表; 早年条目没有这一行."""
    value = extract_text(
        html,
        f"{_INFO}//li[.//span[@class='spec-title'][normalize-space()='配信日']]//span[@class='spec-content']/text()",
    )
    return value or None


def _confirms_number(text: str, number: str) -> bool:
    """页面自报番号与入参一致才算命中; 页面没写时以 URL 为准."""
    found = _PAGE_NUMBER.search(text)
    return found is None or found["number"].casefold() == number.casefold()


def _trailer_urls(text: str) -> list[str]:
    for pattern in _SAMPLE_URLS:
        found = pattern.search(text)
        if found is not None:
            return [found["url"].replace("\\/", "/")]
    return []


def _minutes(value: str) -> int | None:
    """ISO-8601 时长 → 分钟; 缺省或不足一分钟视为没有时长."""
    found = _DURATION.search(value or "")
    if found is None:
        return None
    total = int(found["hours"] or 0) * 60 + int(found["minutes"] or 0)
    return total or None


__all__ = ["CaribbeanComCrawler"]
