"""HEYZO 官网 (www.heyzo.com / en.heyzo.com).

番号形如 `HEYZO-NNNN`, 站内 id 是 4 位补零序号; 详情是 `/movie/<NNNN>`, 与 id 一一对应:
`/movie/0001` 可用, 而 `/movie/1` 与 `/movie/00001` 都是 404, 未知 id 不把无关作品拼成详情页.
旧路径 `/moviepages/<id>/index.html` 仍一律 301 到新路径, 两种写法等价.

站点在 Cloudflare 后面, 但不做挑战: **缺 `Accept` 头时 Apache 直接回 404**, 补上即 200.
判定浏览器与否只看这一个头, 不看 UA / Accept-Language / Referer. 因此 profile 里显式给 `Accept`,
不能依赖 http 客户端默认值 (curl 默认的 `*/*` 与完全不带该头的效果不同, 后者会被判成非浏览器).

页面没有 JSON-LD 与稳定的 data 属性: 标题 / 简介 / 封面 / 预告片取 `og:` 元标签,
出演与配信日 / 配信期間、标签靠「标签文字 + 相邻兄弟节点」定位, 不锚定带哈希的 CSS-module 类名.
新条目把「配信日」换成「配信期間」(区间), 这类条目 release 取区间起点; 区间形态不参与
「配信日」的取值, 两者是互斥的标签.

预告片是 HLS 单档 `og:video`, 不转封装. 站点没有竖版海报: `player_thumbnail.jpg` 是横版封面,
因此只填 `thumb_urls`, 竖版由刮削流程裁切. 剧照与封面同目录层级: 页面只引用 `gallery/thumbnail_NNN.jpg`,
原图是 `gallery/NNN.jpg` (1280×853), 按缩略图序号推导. 页内没有时长字段, runtime 恒为空.
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

# 站内 id 是 4 位补零序号; 少位或多位都不拼地址.
_ID = re.compile(r"[0-9]{4}")
_STUDIO = "HEYZO"
# 缺该头时站点回 404 而不是 403, 因此必须在 profile 里给默认值.
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
# 「配信日 2025年6月10日」与「配信期間 2026年11月14日～22日」共用起始日期形态.
_START_DATE = re.compile(r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")


class HeyzoCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.HEYZO,
            base_url="https://www.heyzo.com",
            headers={"Accept": _ACCEPT},
        )

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        # 只有一次请求: 基类的 _search → _scrape 会对同一页取两次.
        url = self._detail_url(query.number)
        if url is None:
            return None
        return await self._load(url)

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """根路径是年龄门; 详情页需要具体 id, 因此探一个固定存在的条目."""
        return await self.client.check(f"{self.base_url}/movie/0001", cookies=self.cookies, headers=self.headers)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return await self._load(url)

    def _detail_url(self, number: str) -> str | None:
        """只认带本站前缀的番号: 裸 4 位序号属于别的站的 id 空间, 不发请求."""
        value = number.strip()
        prefix, _, suffix = value.partition("-")
        if prefix.casefold() != "heyzo" or _ID.fullmatch(suffix) is None:
            return None
        return f"{self.base_url}/movie/{suffix}"

    async def _load(self, url: str) -> MediaMetadata | None:
        try:
            text = await self.client.get_text(url, cookies=self.cookies, headers=self.headers)
        except RequestError as exc:
            # 未知 id 是未命中, 其余失败照旧上报.
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        return to_metadata(text, url)


def to_metadata(text: str, url: str) -> MediaMetadata | None:
    html = Selector(text=text)
    title = _og(text, "og:title") or extract_text(html, "//title/text()")
    # 未知 id 的 404 页同样带 og:title / og:description (标题写作 "Not Found"),
    # 但没有封面与预告片这两个只有条目页才有的标签, 因此以它们判定命中.
    cover = _og(text, "og:image")
    if not title or not cover:
        return None
    return MediaMetadata(
        number=_number_from_url(url),
        title=_strip_suffix(title),
        actors=film_actors(_actors(html)),
        studio=_STUDIO,
        release=_release(html),
        # 页内没有时长字段.
        runtime=None,
        tags=extract_all_texts(html, _TAG_LINKS),
        plot=_og(text, "og:description") or None,
        thumb_urls=_one(cover),
        trailer_urls=_one(_og(text, "og:video")),
        extrafanart=_gallery_urls(text, cover),
        source_url=url,
    )


# 标签文字的相邻兄弟里找链接: 标签用 span 包文字, 值在同级的 span / div 内.
_ACTORS = '//span[normalize-space()="出演"]/following-sibling::span//a/text()'
_DATE_LABELS = ("配信日", "配信期間")
_TAG_LINKS = '//span[normalize-space()="タグ"]/following-sibling::div//a/text()'


def _og(text: str, prop: str) -> str:
    """og 标签按属性精确取, 不用 XPath 只是为了避开 meta 自闭合写法的差异."""
    found = re.search(rf'<meta property="{re.escape(prop)}" content="(?P<value>[^"]*)"', text)
    return found["value"].strip() if found else ""


def _strip_suffix(title: str) -> str:
    """标题统一带 " | HEYZO" 后缀, 去掉后再比对与展示."""
    head, sep, _ = title.rpartition(" | ")
    return (head if sep else title).strip()


def _number_from_url(url: str) -> str:
    """URL 里的 id 就是番号主体: /movie/0001 → HEYZO-0001."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return f"HEYZO-{tail}"


def _actors(html: Selector) -> list[str]:
    return [name for name in extract_all_texts(html, _ACTORS) if name]


def _gallery_urls(text: str, cover: str) -> list[str]:
    """剧照原图与封面同级: 页面只引用 gallery/thumbnail_NNN.jpg, 原图是同序号的 gallery/NNN.jpg."""
    if "/images/" not in cover:
        return []
    base = cover.rsplit("/images/", 1)[0]
    prefix = base.split("://", 1)[-1].split("/", 1)[-1]
    pattern = re.compile(rf"{re.escape(prefix)}/gallery/thumbnail_(\d{{3}})\.jpg")
    indexes = sorted(set(pattern.findall(re.sub(r"%2[fF]", "/", text))))
    return [f"{base}/gallery/{index}.jpg" for index in indexes]


def _release(html: Selector) -> str | None:
    """配信日与配信期間互斥: 前者是单日, 后者是区间, 两者都取区间的起点."""
    for label in _DATE_LABELS:
        raw = extract_text(html, f'//span[normalize-space()="{label}"]/following-sibling::text()')
        found = _START_DATE.search(raw)
        if found is not None:
            return f"{found['year']}-{int(found['month']):02d}-{int(found['day']):02d}"
    return None


def _one(value: str) -> list[str]:
    return [value] if value else []


__all__ = ["HeyzoCrawler", "to_metadata"]
