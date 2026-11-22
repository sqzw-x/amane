import re
from urllib.parse import urlparse

from parsel import Selector

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome
from ...net.errors import FailureReason, SourceError
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text, is_same_number

# 排除马赛克破坏版等无有效元数据的条目
_EXCLUDED_KEYWORDS = ("克破", "无码破解", "無碼破解", "无码流出", "無碼流出")

_LANG_PREFIX = {
    "zh_cn": "/cn",
    "zh_tw": "",
    "jp": "/jp",
    "en": "/en",
}

#: 站点按 ``Accept-Language`` 判定访问者地区: 落在中国大陆以外时非中文取值对任何语言前缀都回 404 空页,
#: 因此四个前缀共用同一个中文取值.
_HEADERS = {"Accept-Language": "zh-CN,zh;q=0.9"}

#: 域名失效后站点对任何路径都回 200 的跳转壳: 没有 title, 只有一行 JS 转到 /lander.
#: 正常页面也用窗口跳转做站内导航, 因此只认落到 /lander 的那一行.
_LANDER_LINK = re.compile(r'window\.location\.href\s*=\s*"/lander')

#: 详情页没有独立的时长栏, 总秒数只内嵌在播放器配置的 ``all_vtt`` 文件名末端.
_RUNTIME_VTT = re.compile(r'all_vtt\s*:\s*"[^"]*-(\d+)\.vtt"')

#: 简介段落的前缀随语言变化 (简介 / 簡介 / Overview), 取值时去掉.
_PLOT_LABEL = re.compile(r"(?:简介|簡介|Overview)\s*[:：]")


class IqqtvCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.IQQTV, base_url="https://iqqbl.com", traits=frozenset({SourceTrait.MULTI_LANGUAGE})
        )

    def _get_lang_prefix(self, options: FetchOptions | None) -> str:
        lang = (options.language if options else None) or "zh_cn"
        return _LANG_PREFIX.get(lang, "/cn")

    async def check_connectivity(self) -> ConnectivityOutcome:
        """站点轮换域名后旧域名对任何路径都回 200 的跳转壳, 必须核对正文才知道入口是否还在.

        探检索入口: 空查询的检索页会 302 回首页, 跳转被跟随, 因此正文与首页一致而报出的地址是真实入口.
        """
        url = f"{self.base_url}{_LANG_PREFIX['zh_cn']}/search.php"
        try:
            await self._get_html(url)
        except SourceError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=exc.url or url, http_status=exc.http_status)
        return ConnectivityOutcome.ok(url, None)

    async def _get_html(self, url: str) -> str:
        """站点 HTML 的唯一入口: 补语言头, 并把失效域名的跳转壳判为来源失败而非未命中."""
        text = await self.client.get_html(url, headers=_HEADERS)
        if _LANDER_LINK.search(text) is not None:
            raise SourceError(FailureReason.EMPTY_RESPONSE, url=url)
        return text

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """条目标题里的番号是独立词, 只认整词与入参相等; 排除破坏版等条目."""
        number = query.number if re.match(r"n\d{4}", query.number) else query.number.upper()
        prefix = self._get_lang_prefix(options)
        url = f"{self.base_url}{prefix}/search.php?kw={number}"
        html = Selector(text=await self._get_html(url))
        for item in html.xpath('//span[@class="title"]'):
            href = item.xpath("./a/@href").get()
            title = item.xpath("./a/@title").get() or ""
            if not href or any(kw in title for kw in _EXCLUDED_KEYWORDS):
                continue
            if any(is_same_number(token, number) for token in title.split()):
                detail_path = re.sub(r"^/(cn|jp|en)/", "", href).lstrip("/")
                return f"{self.base_url}{prefix}/{detail_path}"
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        html = Selector(text=await self._get_html(url))

        # 标题; 破坏版视为未命中.
        title = extract_text(html, '//h1[@class="h4 b"]/text()')
        if not title or any(kw in title for kw in _EXCLUDED_KEYWORDS):
            return None

        number = extract_text(html, '//div[@class="number"]/text()') or self._extract_number(title)
        title = self._clean_title(title)

        actors = extract_all_texts(html, '//div[contains(@class,"tag-info")]//a[contains(@href,"actor")]/span/text()')
        studio = extract_text(html, '//a[contains(@href, "fac")]/div[@itemprop]/text()')
        release = extract_text(html, '//div[@class="date"]/text()').replace("/", "-").strip()
        runtime = self._parse_runtime(html)
        tags = extract_all_texts(html, '//div[contains(@class,"tag-info")]//a[contains(@href, "tag")]/text()')
        series = extract_text(html, '//a[contains(@href, "series")]/text()')
        plot = self._extract_plot(html)
        thumb_url = extract_text(html, '//meta[@property="og:image"]/@content')
        # 剧照是分段列表里每段的抽帧 (320x240): 站上没有更大的样本图.
        extrafanart = extract_all_texts(html, '//li[contains(@class,"vdplay01")]//div[@class="cover"]//img/@data-src')

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            publisher=studio or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            trailer_urls=self._trailer_urls(html, url),
            extrafanart=extrafanart,
            source_url=url,
        )

    @staticmethod
    def _extract_number(title: str) -> str:
        parts = title.split(" ")
        number = parts[-1] if len(parts) > 1 else title
        return (
            number.replace("_1pondo_", "")
            .replace("1pondo_", "")
            .replace("caribbeancom-", "")
            .replace("caribbeancom", "")
            .replace("-PPV", "")
            .strip(" _-")
        )

    @staticmethod
    def _clean_title(title: str) -> str:
        parts = title.strip().split(" ")
        if len(parts) > 1 and len(parts[-1]) < 5:
            parts.pop()
        if len(parts) > 1:
            parts.pop()
        return " ".join(parts).strip()

    @staticmethod
    def _parse_runtime(html: Selector) -> int | None:
        """分钟. 首选播放器配置里的总秒数, 取不到时退到分段列表的最后一个 ``end``."""
        script = extract_text(html, '//script[contains(., "all_vtt")]/text()')
        match = _RUNTIME_VTT.search(script)
        if match is not None:
            return max(1, round(int(match.group(1)) / 60))
        ends = [int(end) for end in html.xpath('//li[contains(@class,"vdplay01")]/@end').getall() if end.isdigit()]
        return max(1, round(max(ends) / 60)) if ends else None

    @staticmethod
    def _trailer_urls(html: Selector, url: str) -> list[str]:
        """预告片: 站内 php 端点 302 到 CDN 上的 mp4, 下载通道跟随跳转.

        页面给的是带语言前缀的站内路径, 因此只取 URL 的源 (``scheme://host``) 拼接.
        """
        path = extract_text(html, '//div[@id="play_btn"]/@data-preview-video-url')
        if not path:
            return []
        origin = urlparse(url)
        return [f"{origin.scheme}://{origin.netloc}{path}"]

    @staticmethod
    def _extract_plot(html: Selector) -> str:
        text = extract_text(html, '//div[contains(@class,"intro")]/p/text()')
        if not text or any(kw in text for kw in _EXCLUDED_KEYWORDS):
            return ""
        return _PLOT_LABEL.sub("", re.sub(r"[\n\t]", "", text)).strip()
