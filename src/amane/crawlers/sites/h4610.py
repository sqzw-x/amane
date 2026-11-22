"""h4610 站群: エッチな4610 / エッチな0930 / エッチな0930 (c0930) 三个同模板站.

三站共用一套模板与 schema.org Movie JSON-LD, 只差站名与女优池, 因此收在一个模块;
与同集团的 d2pass 站群不同, 这里没有 /dyn/phpauto/ JSON 接口, 详情只有静态页.

详情页 /moviepages/<id>/index.html; 不存在的 id 不返回 404 而是 302 到首页,
因此必须用页面自报的 id 确认命中, 不能凭状态码判定.

同一个 id 在三站是不同作品 (站点各自编号), 归属只由番号前缀 (H4610- / C0930- / H0930-)
与路由决定, 不能跨站重试.

页面是 euc-jp 且正文偶有坏字节, 按替换解码. 站点没有竖版海报: image 是 960x540 横版封面,
女优头像 thumb_s.jpg 是 200x200 方图, 不作封面. 因此只填 thumb_urls, 竖版由刮削流程裁切.
预告片取 video.contentUrl, 早年条目没有该字段.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar, override

from parsel import Selector

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome
from ...net.errors import FailureReason, RequestError
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_text

# 番号形如 H4610-ki221218: 站名前缀 + 短横线 + 站内 id. 只认本站前缀, 不认裸 id.
_NUMBER = re.compile(r"(?P<site>H4610|C0930|H0930)-(?P<id>[A-Za-z0-9]+)")
# 详情页自报的站内 id; 与入参不一致说明被重定向到了首页或别的条目.
_OWN_ID = re.compile(r"movie_id\s*:\s*'(?P<id>[A-Za-z0-9]+)'")
# ISO-8601 时长, 站点写成 PT00H41M17S.
_DURATION = re.compile(r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?")


class H4610Crawler(Crawler):
    """エッチな4610. 另两站的番号前缀与站名由子类覆盖."""

    number_prefix: ClassVar[str] = "H4610"
    studio: ClassVar[str] = "エッチな4610"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.H4610, base_url="https://www.h4610.com")

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        # 只有一次请求: 基类的 _search → _scrape 会对同一页取两次.
        url = self._detail_url(query.number)
        if url is None:
            return None
        return await self._load(url)

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """首页是年龄门与导航; 详情页需要具体 id, 因此探首页."""
        return await self.client.check(f"{self.base_url}/", cookies=self.cookies, headers=self.headers)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return await self._load(url)

    def _detail_url(self, number: str) -> str | None:
        match = _NUMBER.fullmatch(number.strip())
        if match is None or match["site"] != self.number_prefix:
            return None
        return f"{self.base_url}/moviepages/{match['id']}/index.html"

    async def _load(self, url: str) -> MediaMetadata | None:
        movie_id = url.split("/moviepages/", 1)[-1].split("/", 1)[0]
        try:
            text = await self.client.get_html(url, cookies=self.cookies, headers=self.headers, encoding="euc-jp")
        except RequestError as exc:
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        return to_metadata(text, movie_id, url, studio=self.studio)


def to_metadata(text: str, movie_id: str, url: str, *, studio: str) -> MediaMetadata | None:
    found = _OWN_ID.search(text)
    if found is None or found["id"].casefold() != movie_id.casefold():
        return None
    html = Selector(text=text)
    ld = _json_ld(text)
    title = extract_text(html, '//div[@class="moviePlay_title"]//h1//text()')
    if not title or not ld:
        return None
    return MediaMetadata(
        number=movie_id,
        title=title,
        actors=film_actors(_actors(ld)),
        studio=studio,
        release=_release(ld),
        runtime=_minutes(str(ld.get("duration") or "")),
        tags=_tags(html),
        plot=_text(ld.get("description")) or None,
        thumb_urls=_images(ld.get("image")),
        trailer_urls=_trailer(ld),
        source_url=url,
    )


def _json_ld(text: str) -> dict[str, object]:
    """页面只有一个 schema.org Movie 块; 解析失败按无数据处理."""
    found = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    if found is None:
        return {}
    try:
        data = json.loads(found[1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _actors(ld: dict[str, object]) -> list[str]:
    """actor 可能是单个 Person 或列表, 只取其中的 name."""
    actor = ld.get("actor")
    items = actor if isinstance(actor, list) else [actor]
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = _text(item.get("name"))
            if name:
                names.append(name)
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


def _release(ld: dict[str, object]) -> str | None:
    value = _text(ld.get("dateCreated"))
    return value[:10] if len(value) >= 10 else (value or None)


def _tags(html: Selector) -> list[str]:
    """プレイ内容 是唯一的结构化标签; 站点没有系列字段."""
    raw = extract_text(html, '//dt[normalize-space()="プレイ内容"]/following-sibling::dd[1]/text()')
    return [part for part in re.split(r"[\s\u3000]+", raw) if part]


def _images(value: object) -> list[str]:
    """image 是 960x540 横版封面, 偶有 // 开头的相对协议地址."""
    url = _text(value)
    if not url:
        return []
    return [f"https:{url}" if url.startswith("//") else url]


def _trailer(ld: dict[str, object]) -> list[str]:
    video = ld.get("video")
    if not isinstance(video, dict):
        return []
    url = _text(video.get("contentUrl"))
    return [url] if url else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _minutes(value: str) -> int | None:
    """ISO-8601 时长 → 分钟; 缺省或不足一分钟视为没有时长."""
    found = _DURATION.match(value or "")
    if found is None:
        return None
    total = int(found["hours"] or 0) * 60 + int(found["minutes"] or 0)
    return total or None


class C0930Crawler(H4610Crawler):
    """エッチな0930 (人妻系)."""

    number_prefix = "C0930"
    studio = "エッチな0930"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.C0930, base_url="https://www.c0930.com")


class H0930Crawler(H4610Crawler):
    """エッチな0930 (熟女系)."""

    number_prefix = "H0930"
    studio = "エッチな0930"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.H0930, base_url="https://www.h0930.com")


__all__ = ["C0930Crawler", "H0930Crawler", "H4610Crawler", "to_metadata"]
