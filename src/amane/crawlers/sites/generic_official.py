"""厂商官网的通用兜底源: 按标准位置提取, 不依赖逐站手写适配.

欧美小厂牌的官网大多是 WordPress, 页面里通常至少有一处标准结构化数据:

    1. ``application/ld+json`` 里的 VideoObject / Movie (最可靠, 带日期与描述)
    2. ``og:title`` / ``og:description`` / ``og:image`` / ``og:video``
    3. ``wp-content/uploads/.../*-teaser.mp4`` 这类约定俗成的预告片路径

这些位置命中率高, 命中不了就不产出, 不猜字段. 输入是上游来源给出的官网链接
(TPDB 的 ``scene.urls``), 因此它不负责"找官网", 只负责"把官网读懂".

不调模型: 规则是固定的, 同一份正文永远得到同一份结果. 后续若要在标准位置全失败时
才降级到模型生成选择器, 也应当保持"生成配置、不生成代码"的形态.
"""

from __future__ import annotations

import json
import re
from typing import Any, override

from parsel import Selector

from ...enums import SiteName
from ...net.errors import FailureReason, RequestError
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import extract_text

#: 单页最多尝试的条目数: 官网列表页比详情页大得多, 取前几个够用.
_MAX_URLS = 3
#: ``wp-content/uploads/.../xxx-teaser.mp4`` 之类的约定路径.
_TEASER = re.compile(r"https?://[^\"'\s\\]+/(?P<name>[^/\"']*teaser[^/\"']*)\.mp4", re.IGNORECASE)
#: 官网常见的发布日期写法; JSON-LD 与 meta 都可能给.
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _json_ld(selector: Selector) -> dict[str, Any]:
    """取第一个可解析的 JSON-LD 对象; 列表形态取首项."""
    for raw in selector.xpath('//script[@type="application/ld+json"]/text()').getall():
        try:
            data = json.loads(raw)
        except ValueError, TypeError:
            continue
        for candidate in data if isinstance(data, list) else [data]:
            if isinstance(candidate, dict):
                return candidate
    return {}


def _og(selector: Selector, name: str) -> str | None:
    value = selector.xpath(f'//meta[@property="{name}"]/@content').get()
    return value.strip() if value and value.strip() else None


def _meta_date(selector: Selector) -> str | None:
    """发布日期: JSON-LD 优先, 没有时退回 article meta."""
    published = selector.xpath('//meta[@property="article:published_time"]/@content').get()
    return published[:10] if published and _DATE.match(published) else None


def _parse_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    found = _DATE.search(value)
    return found.group(0) if found else None


def _actors(selector: Selector, note: str | None, ld: dict[str, Any]) -> list[FilmActor]:
    """演员: JSON-LD 的 ``actor`` / ``actors`` 优先, 否则从描述里的 "features X and Y" 取."""
    raw = ld.get("actor") or ld.get("actors")
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            name = item.get("name") if isinstance(item, dict) else item
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    elif isinstance(raw, dict) and isinstance(raw.get("name"), str):
        names.append(raw["name"].strip())
    if names:
        return [FilmActor(name=name) for name in dict.fromkeys(names)]
    if not note:
        return []
    found = re.search(r"(?:features|starring)\s+(.+?)(?:\.|,|\s+in\s+)", note, re.IGNORECASE)
    if not found:
        return []
    parts = [part.strip() for part in re.split(r",|\band\b", found.group(1)) if part.strip()]
    return [FilmActor(name=part) for part in dict.fromkeys(parts)]


class GenericOfficialCrawler(Crawler):
    """把上游给出的官网链接读懂; 标准位置全失败时返回 None, 不猜."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.OFFICIAL_GENERIC,
            # 官网五花八门, 不设固定 base_url; 出站域名取下游给的具体链接.
            base_url="",
            traits=frozenset({SourceTrait.NEEDS_PARTIAL}),
        )

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        partial = query.partial_result
        if partial is None:
            return None
        for url in partial.homepage_urls[:_MAX_URLS]:
            try:
                meta = await self._extract(url, partial.number)
            except RequestError as exc:
                if exc.reason is FailureReason.NOT_FOUND:
                    continue
                raise
            if meta is not None:
                return meta
        return None

    async def _extract(self, url: str, number: str) -> MediaMetadata | None:
        text = await self._text(url)
        if text is None:
            return None
        selector = Selector(text=text)
        ld = _json_ld(selector)
        title = _text_of(ld.get("name")) or _og(selector, "og:title") or _page_title(selector)
        plot = _text_of(ld.get("description")) or _og(selector, "og:description")
        release = _parse_date(ld.get("uploadDate")) or _parse_date(ld.get("datePublished")) or _meta_date(selector)
        thumb = _og(selector, "og:image") or _text_of(ld.get("thumbnailUrl"))
        trailer = _og(selector, "og:video") or _trailer_of(ld) or _teaser_of(text)
        actors = _actors(selector, plot, ld)
        if not any((title, plot, release, thumb, trailer, actors)):
            # 一条可用字段都没有: 这个页面不是作品页, 别把空壳当命中.
            return None
        return MediaMetadata(
            number=number,
            title=title,
            plot=plot,
            release=release,
            actors=actors,
            thumb_urls=[thumb] if thumb else [],
            trailer_urls=[trailer] if trailer else [],
            source_url=url,
        )

    async def _text(self, url: str) -> str | None:
        try:
            return await self.client.get_text(url)
        except RequestError as exc:
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None


def _text_of(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _page_title(selector: Selector) -> str | None:
    return _text_of(extract_text(selector, "//title/text()"))


def _trailer_of(ld: dict[str, Any]) -> str | None:
    for key in ("contentUrl", "video", "trailer"):
        value = ld.get(key)
        if isinstance(value, str) and value.endswith(".mp4"):
            return value
        if isinstance(value, dict):
            found = _text_of(value.get("contentUrl"))
            if found and found.endswith(".mp4"):
                return found
    return None


def _teaser_of(text: str) -> str | None:
    found = _TEASER.search(text)
    return found.group(0) if found else None


__all__ = ["GenericOfficialCrawler"]
