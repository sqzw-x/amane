import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from ..parsing import extract_all_texts, extract_text

# 排除马赛克破坏版等无有效元数据的条目
_EXCLUDED_KEYWORDS = ("克破", "无码破解", "無碼破解", "无码流出", "無碼流出")

# 语言 → URL 路径前缀
_LANG_PREFIX = {
    "zh_cn": "/cn",
    "zh_tw": "",
    "jp": "/jp",
}


class IqqtvCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.IQQTV, base_url="https://iqq5.xyz", multi_language=True)

    def _get_lang_prefix(self, options: FetchOptions | None) -> str:
        """从 FetchOptions.language 获取 URL 路径前缀, 默认 /cn."""
        lang = (options.language if options else None) or "zh_cn"
        return _LANG_PREFIX.get(lang, "/cn")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number if re.match(r"n\d{4}", query.number) else query.number.upper()
        prefix = self._get_lang_prefix(options)
        url = f"{self.base_url}{prefix}/search.php?kw={number}"
        text = await self.client.get_html(url)
        html = Selector(text=text)
        # 检查搜索结果是否存在
        if not html.xpath('//a[@class="ga_click"]/@href').getall():
            return None
        # 遍历搜索结果, 匹配番号且排除无效条目
        for item in html.xpath('//span[@class="title"]'):
            href = item.xpath("./a/@href").get()
            title = item.xpath("./a/@title").get() or ""
            if not href:
                continue
            if number.upper() in title and not any(kw in title for kw in _EXCLUDED_KEYWORDS):
                # 规范化详情页 URL
                detail_path = re.sub(r"^/(cn|jp)/", "", href).lstrip("/")
                return f"{self.base_url}{prefix}/{detail_path}"
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)
        html = Selector(text=text)

        title = extract_text(html, '//h1[@class="h4 b"]/text()')
        if not title or "克破" in title:
            return None

        # 从标题中提取番号
        number = self._extract_number(title)
        # 标题去除尾部番号
        title = self._clean_title(title)

        actors = extract_all_texts(html, '//a[contains(@href, "actor")]/span/text()')
        studio = extract_text(html, '//a[contains(@href, "fac")]/div[@itemprop]/text()')
        release = extract_text(html, '//div[@class="date"]/text()')
        if release:
            release = release.replace("/", "-").strip()
        runtime = self._parse_runtime(html)
        tags = extract_all_texts(html, '//div[contains(@class,"tag-info")]//a[contains(@href, "tag")]/text()')
        series = extract_text(html, '//a[contains(@href, "series")]/text()')
        thumb_url = extract_text(html, '//meta[@property="og:image"]/@content')
        plot = self._extract_plot(html)
        extrafanart = html.xpath('//div[@class="cover"]//img[@src]/@data-src').getall()

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=actors,
            studio=studio or None,
            publisher=studio or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            plot=plot or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            extrafanart=extrafanart,
            source_url=url,
        )

    @staticmethod
    def _extract_number(title: str) -> str:
        """从标题末尾提取番号."""
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
        """去除标题尾部的番号和短后缀."""
        parts = title.strip().split(" ")
        if len(parts) > 1 and len(parts[-1]) < 5:
            parts.pop()
        # 去除末尾番号部分
        if len(parts) > 1:
            parts.pop()
        return " ".join(parts).strip()

    @staticmethod
    def _parse_runtime(html: Selector) -> int | None:
        """解析 ISO 8601 duration 格式的时长."""
        duration = extract_text(html, '//meta[@itemprop="duration"]/@content')
        if not duration:
            return None
        # 格式: "HH:MM:SS"
        parts = duration.strip().split(":")
        if len(parts) == 3:
            try:
                return int(parts[0]) * 60 + int(parts[1]) + (1 if int(parts[2]) >= 30 else 0)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_plot(html: Selector) -> str:
        """提取简介, 清理无效内容."""
        text = extract_text(html, '//p[contains(., "简介") or contains(., "簡介")]/text()')
        if not text or "克破" in text:
            return ""
        # 去除无意义信息
        text = re.sub(r"[\n\t]|(简|簡)介：", "", text)
        return text.split("*根据分发", 1)[0].strip()
