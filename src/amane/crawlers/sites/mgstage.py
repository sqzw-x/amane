"""MGStage. 站内检索只做 token 前缀匹配, 番号前多写的一段数字无法由入参推导.

站点把部分系列写成 ``107STKO-005`` / ``529STCV-605``: 直达与按 ``STKO-005`` 检索都落空, 按尾号 ``005`` 检索
的目标又不在首页结果里. 检索按站点商品目录 (三份 ``product_detail_sitemap*.xml.gz``) → 直连候选 → 站内
检索的顺序进行, 每一档都按声明的番号核对, 不取首条.
"""

from __future__ import annotations

import asyncio
import gzip
import re
import time
import zlib
from dataclasses import dataclass
from html import unescape as html_unescape
from typing import TYPE_CHECKING

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import (
    extract_all_texts,
    extract_text,
    number_variants,
    select_number_index,
    without_leading_digits,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...config import SiteConfig
    from ..http import HttpClient

#: 竖版海报与横版放大图同目录同名, 只差文件名前缀; 详情页只引用后者, 海报地址由封面推导.
_COVER_PREFIX = "pb_e_"
_POSTER_PREFIX = "pf_e_"
#: 素人条目的出演栏写的是站点的代名 (名字后跟年龄与职业), 不是可对齐的演员身份.
_AMATEUR_TAG = "素人"
#: 番号尾部的数字段, 供站点多写了一段前导数字时反查.
_TAIL_DIGITS = re.compile(r"(\d+)\s*$")
#: 详情页只引用播放器页, pid 在其路径末段; 真正的 mp4 地址要再用 pid 换一次.
_SAMPLE_PID = re.compile(r"/sampleplayer/sampleplayer\.html/([^/?#\"']+)")
_SAMPLE_RESPONSE = "https://www.mgstage.com/sampleplayer/sampleRespons.php?pid={pid}"
#: 站点播放器脚本把 ``.ism/request?...`` 改写成 ``.mp4``; 服务端拿到的是播放列表形态.
_ISM_REQUEST = re.compile(r"\.ism/request.*$")

#: 详情页地址里的番号段, 检索结果与直达地址共用同一形态.
_DETAIL_PATH = re.compile(r"/product/product_detail/([^/]+)/")
#: 商品目录的三份分片, 合计十余万条.
_SITEMAP_SHARDS: tuple[str, ...] = (
    "product_detail_sitemap1.xml.gz",
    "product_detail_sitemap2.xml.gz",
    "product_detail_sitemap3.xml.gz",
)
#: 索引有效期. 到期后重新拉取全量目录; 拉取失败时沿用旧索引.
_INDEX_TTL_S = 24 * 3600
#: 索引不可用后的退避间隔: 期间直接走直连与检索兜底, 不让每次刮削各付一次拉取超时.
_INDEX_RETRY_S = 600
_LOC = re.compile(rb"<loc>\s*https://www\.mgstage\.com/product/product_detail/([^/\s<]+)/\s*</loc>")


class CatalogUnusable(Exception):
    """目录正文不合读模型. 只降级到下一档检索, 不当作来源失败."""


@dataclass(frozen=True, slots=True)
class CatalogIndex:
    """商品目录: 去掉开头数字段的折叠番号 → 站点上的完整番号 (可能不止一个).

    裸番号同样入索引: 站点上 ``CHA-010`` 与 ``031CHA-010`` 是两部不同作品, 只收带数字段的会让裸番号
    被同尾号的那部抢走. 同一尾部对应多个番号时全部保留, 由 ``select_number_index`` 取舍.
    """

    by_tail: Mapping[str, tuple[str, ...]]

    def candidates(self, number: str) -> tuple[str, ...]:
        return self.by_tail.get(without_leading_digits(number), ())


@dataclass(frozen=True, slots=True)
class IndexResult:
    """索引档的结论.

    ``hit`` 是命中地址; ``ambiguous`` 表示站点上确有同尾号的不同作品, 猜一个会返回无关影片;
    两者皆空表示索引不可用或未收录, 退到后两档.
    """

    hit: str | None = None
    ambiguous: bool = False


def _decode(payload: bytes) -> bytes:
    """分片按 ``application/x-gzip`` 原样下发; 也收已解压的正文 (夹具给的是解压后的)."""
    if payload[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(payload)
        except (OSError, EOFError) as exc:
            raise CatalogUnusable(f"sitemap shard is not valid gzip: {exc}") from exc
    if payload[:1] == b"\x78":
        try:
            return zlib.decompress(payload)
        except zlib.error as exc:
            raise CatalogUnusable(f"sitemap shard is not valid deflate: {exc}") from exc
    return payload


def parse_catalog(payloads: Sequence[bytes]) -> CatalogIndex:
    """若干目录分片 → 索引.

    正文不是 sitemap (拦截页 / 首页 / 损坏的分片) 时抛 ``CatalogUnusable``, 调用方降级到下一档.
    正文是 sitemap 但没有任何 product_detail 条目时返回空索引: 那是「目录里没有番号」, 不是失败.
    """
    if not payloads:
        raise CatalogUnusable("no sitemap shard")
    found: dict[str, list[str]] = {}
    for payload in payloads:
        raw = _decode(payload)
        if b"<loc>" not in raw:
            raise CatalogUnusable("sitemap shard carries no <loc>")
        for match in _LOC.finditer(raw):
            number = match.group(1).decode("utf-8", "replace")
            numbers = found.setdefault(without_leading_digits(number), [])
            if number not in numbers:
                numbers.append(number)
    return CatalogIndex(by_tail={key: tuple(numbers) for key, numbers in found.items()})


def _poster_url(cover_url: str) -> str | None:
    """横版封面地址 → 同目录竖版海报地址; 非 ``pb_e_`` 形态返回 ``None``."""
    directory, _, filename = cover_url.rpartition("/")
    if not directory or not filename.startswith(_COVER_PREFIX):
        return None
    return f"{directory}/{_POSTER_PREFIX}{filename.removeprefix(_COVER_PREFIX)}"


def _sample_mp4(url: str) -> str:
    """播放器声明的样品地址 → 可直接下载的 mp4; 站点在 JSON 里把 ``&`` 转义成 ``&amp;``."""
    resolved = _ISM_REQUEST.sub(".mp4", html_unescape(url))
    # 早期条目在 ``dl`` 子域, 播放器脚本同样换成 ``chdl``.
    return resolved.replace("http://dl", "http://chdl")


class MGStageCrawler(Crawler):
    def __init__(self, client: HttpClient, config: SiteConfig | None = None) -> None:
        super().__init__(client, config)
        self._index: CatalogIndex | None = None
        self._index_at = 0.0
        #: 拉取失败后的退避到期时刻; 期间不再请求目录端点.
        self._retry_at = 0.0
        #: 并发检索只让一个协程拉取索引.
        self._index_lock = asyncio.Lock()

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.MGSTAGE, base_url="https://www.mgstage.com", cookies={"adc": "1"})

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """索引 → 直连候选 → 尾号检索. 三档皆未命中返回 ``None`: 本店没有该番号不是来源失败."""
        indexed = await self._from_index(query.number)
        if indexed.hit is not None:
            return indexed.hit
        if indexed.ambiguous:
            return None
        for number in number_variants(query.number):
            url = await self._search_number(number, query.number)
            if url is not None:
                return url
        # 站点在番号前多写一段数字时 (529STCV-605 对 STCV-605) 上面几条都落空: 站内检索只做
        # token 前缀匹配 (529 / 529STCV / 605 能查到, STCV 查不到), 前缀又无法推导, 因此用尾号再检一次.
        tail = _TAIL_DIGITS.search(query.number)
        if tail is not None and tail.group(1) != query.number:
            return await self._search_keyword(tail.group(1), query.number)
        return None

    def _detail_url(self, number: str) -> str:
        return f"{self.base_url}/product/product_detail/{number}/"

    async def _from_index(self, number: str) -> IndexResult:
        """按去掉开头数字段的番号对齐; 索引不可用时返回空结论, 由后两档兜底.

        同尾号不止一条时仍按 ``select_number_index`` 取舍: 入参本身写着数字段 (``483SGK-004``) 的能精确
        命中; 只有入参没写数字段而站点上确有两部 (``SGK-004`` 对 ``483SGK-004`` / ``902SGK-004``) 才是
        无法确认, 此时判未命中, 不降级 —— 索引已建立时后两档的候选集与这里相同, 降级只多付请求.

        索引本次不可用 (尚未拉取 / 退避中) 是另一回事: 那种情况返回空结论, 由后两档正常兜底.
        """
        index = await self._ensure_index()
        if index is None:
            return IndexResult()
        found = index.candidates(number)
        if not found:
            return IndexResult()
        picked = select_number_index(found, number)
        if picked is None:
            self.logger.debug("catalog index: several entries share the tail", number=number, entries=found)
            return IndexResult(ambiguous=True)
        self.logger.debug("search hit", number=number, via="index", entry=found[picked])
        return IndexResult(hit=self._detail_url(found[picked]))

    async def _search_number(self, number: str, query_number: str) -> str | None:
        # 先尝试直接访问详情页.
        direct_url = self._detail_url(number)
        try:
            text = await self.client.get_html(direct_url, cookies=self.cookies)
        except RequestError:
            text = None
        if text:
            title = extract_text(Selector(text=text), '//*[@id="center_column"]/div[1]/h1/text()')
            if title:
                return direct_url
        return await self._search_keyword(number, query_number)

    async def _search_keyword(self, number: str, query_number: str) -> str | None:
        # 检索结果按条目声明的番号确认, 不取首条.
        search_url = f"{self.base_url}/search/cSearch.php?search_word={number}"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in html.xpath('//a[contains(@href, "/product/product_detail/")]/@href').getall():
            url = f"{self.base_url}{raw}" if raw.startswith("/") else raw
            match = _DETAIL_PATH.search(url)
            if match is None or url in seen:
                continue
            seen.add(url)
            candidates.append((url, match[1]))
        index = select_number_index([found for _, found in candidates], query_number)
        return candidates[index][0] if index is not None else None

    async def _ensure_index(self) -> CatalogIndex | None:
        """索引可用时直接返回; 到期或缺失时拉取一次, 拉取失败保留旧索引 (可能为 ``None``)."""
        if (fresh := self._fresh_index()) is not None:
            return fresh
        if time.time() < self._retry_at:
            return self._index
        async with self._index_lock:
            # 等锁期间可能已被并发调用刷新完, 或已经失败并进入退避: 两者都不能再发一次请求.
            if (fresh := self._fresh_index()) is not None:
                return fresh
            if time.time() < self._retry_at:
                return self._index
            return await self._refresh_index()

    def _fresh_index(self) -> CatalogIndex | None:
        """未过期的索引; 尚未拉取或已过期时为 ``None``."""
        if self._index is None or time.time() - self._index_at >= _INDEX_TTL_S:
            return None
        return self._index

    async def _refresh_index(self) -> CatalogIndex | None:
        """拉一次全量目录. 三份分片串行取: 同 host 的限速器桶容量为 1, 并发只是在它上面排队.
        失败时每个分片各付一次超时 (首个失败即中断), 因此退避 600 秒, 不让每批刮削都重新等一遍.
        """
        try:
            payloads = [await self.client.get_bytes(f"{self.base_url}/{shard}") for shard in _SITEMAP_SHARDS]
            index = await asyncio.to_thread(parse_catalog, payloads)
        except (RequestError, CatalogUnusable) as exc:
            self._retry_at = time.time() + _INDEX_RETRY_S
            # 刷新失败沿用旧索引, 不清空: 目录端点不可用不能让整个来源失效, 后两档检索仍可命中.
            self.logger.warning("catalog index refresh failed", error=str(exc), has_stale=self._index is not None)
            return self._index
        self._index, self._index_at, self._retry_at = index, time.time(), 0.0
        self.logger.info("catalog index ready", numbers=len(index.by_tail))
        return index

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//*[@id="center_column"]/div[1]/h1/text()')
        if not title:
            return None

        number_match = _DETAIL_PATH.search(url)
        number = number_match.group(1) if number_match else ""

        tags = extract_all_texts(html, '//th[contains(text(),"ジャンル")]/following-sibling::td/a/text()')

        # 素人条目的出演值是站点代名; 演员字段按首个非空来源锁定, 写入即顶掉 javdb 记的本人, 故不取.
        actors = (
            []
            if any(_AMATEUR_TAG in tag for tag in tags)
            else extract_all_texts(html, '//th[contains(text(),"出演")]/following-sibling::td/a/text()')
        )
        studio = extract_text(html, '//th[contains(text(),"メーカー")]/following-sibling::td/a/text()')
        publisher = extract_text(html, '//th[contains(text(),"レーベル")]/following-sibling::td/a/text()')
        series = extract_text(html, '//th[contains(text(),"シリーズ")]/following-sibling::td/a/text()')

        runtime_raw = extract_text(html, '//th[contains(text(),"収録時間")]/following-sibling::td/text()')
        runtime = self._parse_runtime(runtime_raw)

        release_raw = extract_text(html, '//th[contains(text(),"配信開始日")]/following-sibling::td/text()')
        release = release_raw.strip() if release_raw else None

        cover = extract_text(html, '//a[@id="EnlargeImage"]/@href', '//img[@class="enlarge_image"]/@src')
        thumb_url = cover or None
        poster_url = _poster_url(cover)

        extrafanart = extract_all_texts(html, '//dl[@id="sample-photo"]//li/a/@href')

        # 样品片需要再换一次地址; 取不到时为空, 不阻断其余字段.
        trailer_urls = await self._trailer_urls(html)

        # 商品紹介的正文有两种标记 (直接在 p.txt.introduction 内, 或嵌在其中的 <p> 被解析器拍平为兄弟节点),
        # 两者都收, 只排除「すべてを見る」.
        plot = "\n".join(
            extract_all_texts(html, '//dl[@id="introduction"]/dd/p[not(contains(@class, "more"))]//text()')
        )

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
            plot=plot or None,
            poster_urls=[poster_url] if poster_url else [],
            thumb_urls=[thumb_url] if thumb_url else [],
            trailer_urls=trailer_urls,
            extrafanart=extrafanart,
            source_url=url,
            external_id=url,
        )

    async def _trailer_urls(self, html: Selector) -> list[str]:
        """预告片.

        详情页只给出播放器页地址, 真正的 mp4 要拿 pid 换 ``sampleRespons.php``; 站点播放器脚本
        再把 ``.ism/request?...`` 改写成 ``.mp4``, 这层改写必须在服务端完成, 否则拿到的是播放列表.
        早期条目没有样品, 不按命名规则构造地址.
        """
        href = extract_text(html, '//p[contains(@class, "sample_movie_btn")]/a/@href')
        found = _SAMPLE_PID.search(href or "")
        if found is None:
            return []
        try:
            payload = await self.client.get_json(_SAMPLE_RESPONSE.format(pid=found[1]), cookies=self.cookies)
        except RequestError:
            # 样品是附加资源: 取不到按没有处理, 不让整条刮削失败.
            self.logger.debug("sample player unavailable", pid=found[1])
            return []
        if not isinstance(payload, dict):
            return []
        url = payload.get("url")
        return [_sample_mp4(url)] if isinstance(url, str) and url else []

    @staticmethod
    def _parse_runtime(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
