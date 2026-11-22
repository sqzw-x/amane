"""JSON API: ``/api/sku/item/{skuId}`` → ``parentProduct.uuid`` → ``/api/product/{uuid}``.

同捆特典 SKU 在番号前加前缀 (``TKTABW-125``), 裸番号未必有 SKU, 只按裸番号直连会漏刮. 检索按 SKU 索引
(``/api/sku/sitemap``) → 直连候选 → ``/api/search`` 兜底的顺序进行, 每一档都用剥离前缀后的番号核对, 不取首条.

站点限定日本 IP: 非日本 IP 下 curl_cffi 得 CloudFront 403, 系统 curl 得地域限制文案.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, override
from urllib.parse import quote

from pydantic import Field, TypeAdapter, ValidationError

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome, probe_get
from ...net.errors import FailureReason
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, ResponseModel, SearchQuery, film_actors
from ..parsing import fold_number, select_number_index

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ...config import SiteConfig
    from ..http import HttpClient

#: 探测样本番号: 必须有裸番号 SKU 且在售. 站点对不存在的 SKU 返回 404, 探测结论会落成失败.
_PROBE_SKU = "ABW-350"

#: 索引有效期. 到期后重新拉取全量索引; 拉取失败时沿用旧索引.
_INDEX_TTL_S = 24 * 3600
#: 索引不可用后的退避间隔: 期间直接走直连与搜索兜底, 不让每次刮削各付一次拉取超时.
_INDEX_RETRY_S = 600


class SkuPrefix(StrEnum):
    """同捆特典 SKU 的前缀, 声明顺序即多 SKU 并存时的取舍顺序 (裸番号永远排在它们之前).

    ``TKT`` 未公開映像DVD付き · ``GOOE`` グッズ · ``CTKT`` 特典チェキ · ``STKT`` A4ポスター＆生写真 ·
    ``PTKT`` オリジナルポスター · ``WTKT`` 写真集 · ``JTKT`` 女優写真集＋未公開映像.
    """

    TKT = "TKT"
    GOOE = "GOOE"
    CTKT = "CTKT"
    STKT = "STKT"
    PTKT = "PTKT"
    WTKT = "WTKT"
    JTKT = "JTKT"


#: 剥离顺序按前缀长度降序. 现有前缀互不为前缀, 顺序不影响结果; 按长度排是给「新前缀包含旧前缀」留出正确性,
#: 否则短的那个先命中, 剥出的不是番号.
_PREFIXES_BY_LENGTH: tuple[SkuPrefix, ...] = tuple(sorted(SkuPrefix, key=lambda item: len(item.value), reverse=True))


class SitemapEntry(ResponseModel):
    """``/api/sku/sitemap`` 的一条. 只解析检索要用的两栏, 其余字段不读."""

    delivery_item_id: str = Field(alias="deliveryItemId")
    product_uuid: str = Field(alias="productUuid")


#: 索引正文的读模型. 4.74 MB / 一万余条, 解析搬到线程里执行, 见 ``PrestigeCrawler._refresh_index``.
_SITEMAP: TypeAdapter[list[SitemapEntry]] = TypeAdapter(list[SitemapEntry])


class SitemapUnusable(Exception):
    """索引正文不合读模型. 只降级到下一档检索, 不当作来源失败."""


@dataclass(frozen=True, slots=True)
class SkuHit:
    """剥离前缀后的一个候选 SKU.

    索引用不可变 dataclass 而不是 pydantic 模型: 全量索引一万余条, 每条多一层校验与字典并不值得;
    外部响应本身仍按 ``SitemapEntry`` 校验.
    """

    sku_id: str
    number: str
    """剥离前缀后的番号; 裸番号 SKU 与 ``sku_id`` 相同."""
    product_uuid: str

    @property
    def prefix(self) -> SkuPrefix | None:
        return _prefix_of(self.sku_id)


@dataclass(frozen=True, slots=True)
class SitemapIndex:
    """``/api/sku/sitemap`` 的内存索引: 折叠番号 → 该番号的全部候选, 已按取舍键排好序."""

    by_number: Mapping[str, tuple[SkuHit, ...]]

    def candidates(self, number: str) -> tuple[SkuHit, ...]:
        return self.by_number.get(fold_number(number), ())


class _SearchSource(ResponseModel):
    delivery_item_id: str | None = Field(default=None, alias="deliveryItemId")
    product_uuid: str | None = Field(default=None, alias="productUuid")


class _SearchHit(ResponseModel):
    source: _SearchSource | None = Field(default=None, alias="_source")


class _SearchHits(ResponseModel):
    hits: list[_SearchHit] = Field(default_factory=list)


class _SearchResponse(ResponseModel):
    hits: _SearchHits | None = None


def _prefix_of(sku_id: str) -> SkuPrefix | None:
    """SKU 携带的同捆特典前缀; 裸番号为 ``None``.

    前缀后须紧跟字母或数字: ``GOOE-155`` 本身就是番号, 不是 ``GOOE`` 前缀加 ``-155``.
    """
    for prefix in _PREFIXES_BY_LENGTH:
        if not sku_id.startswith(prefix.value):
            continue
        rest = sku_id[len(prefix.value) :]
        if rest and rest[0].isalnum():
            return prefix
    return None


def strip_sku_prefix(sku_id: str) -> str:
    """剥离已知的同捆特典前缀; 裸番号原样返回."""
    prefix = _prefix_of(sku_id)
    return sku_id[len(prefix.value) :] if prefix is not None else sku_id


def rank_sku(sku_id: str) -> tuple[int, str]:
    """多 SKU 的取舍键: 裸番号优先, 其后按 ``SkuPrefix`` 的声明顺序; 同档按 SKU 字串定序, 结果与顺序无关."""
    prefix = _prefix_of(sku_id)
    return (0, sku_id) if prefix is None else (list(SkuPrefix).index(prefix) + 1, sku_id)


def preferred_number(sku_ids: Iterable[str]) -> str | None:
    """多个 SKU 中优先级最高的那个的番号: 裸番号优先, 其后按前缀顺序; 无 SKU 时为 ``None``."""
    candidates = [sku_id for sku_id in sku_ids if sku_id]
    return strip_sku_prefix(min(candidates, key=rank_sku)) if candidates else None


def build_index(entries: Sequence[SitemapEntry]) -> SitemapIndex:
    """条目 → 折叠番号索引. 同一番号的多条按取舍键排序, 检索取首条即最优候选."""
    grouped: dict[str, list[SkuHit]] = {}
    for entry in entries:
        hit = SkuHit(
            sku_id=entry.delivery_item_id,
            number=strip_sku_prefix(entry.delivery_item_id),
            product_uuid=entry.product_uuid,
        )
        grouped.setdefault(fold_number(hit.number), []).append(hit)
    return SitemapIndex(
        {key: tuple(sorted(hits, key=lambda hit: rank_sku(hit.sku_id))) for key, hits in grouped.items()}
    )


def parse_sitemap(payload: bytes) -> SitemapIndex:
    """``/api/sku/sitemap`` 正文 → 索引; 不合读模型或为空时抛 ``SitemapUnusable`` (调用方降级到下一档)."""
    try:
        entries = _SITEMAP.validate_json(payload)
    except ValidationError as exc:
        raise SitemapUnusable(f"sitemap does not match the read model: {exc.error_count()} error(s)") from exc
    if not entries:
        raise SitemapUnusable("sitemap is empty")
    return build_index(entries)


def _hit_from_sku(sku_id: str, data: object) -> SkuHit | None:
    """``/api/sku/item`` 应答 → 候选; 应答缺 ``parentProduct.uuid`` 时为 ``None``."""
    if not isinstance(data, dict):
        return None
    parent = data.get("parentProduct")
    product_uuid = parent.get("uuid") if isinstance(parent, dict) else None
    if not isinstance(product_uuid, str) or not product_uuid:
        return None
    delivery_item_id = data.get("deliveryItemId")
    sku = delivery_item_id if isinstance(delivery_item_id, str) and delivery_item_id else sku_id
    return SkuHit(sku_id=sku, number=strip_sku_prefix(sku), product_uuid=product_uuid)


def _parse_search_hits(payload: object) -> list[SkuHit] | None:
    """``/api/search`` 的 Elasticsearch 应答 → 候选; 不合读模型时为 ``None``."""
    try:
        response = _SearchResponse.model_validate(payload)
    except ValidationError:
        return None
    hits: list[SkuHit] = []
    for hit in response.hits.hits if response.hits is not None else ():
        source = hit.source
        if source is None or not source.delivery_item_id or not source.product_uuid:
            continue
        hits.append(
            SkuHit(
                sku_id=source.delivery_item_id,
                number=strip_sku_prefix(source.delivery_item_id),
                product_uuid=source.product_uuid,
            )
        )
    return hits


class PrestigeCrawler(Crawler):
    def __init__(self, client: HttpClient, config: SiteConfig | None = None) -> None:
        super().__init__(client, config)
        self._index: SitemapIndex | None = None
        self._index_at = 0.0
        #: 索引拉取失败后的退避到期时刻; 期间不再请求索引端点.
        self._retry_at = 0.0
        #: 并发检索只让一个协程拉取索引.
        self._index_lock = asyncio.Lock()

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PRESTIGE, base_url="https://www.prestige-av.com")

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """真实入口是 SKU JSON API; 首页带年龄墙, 探测首页会把可用的来源报成不可达.

        探测不取全量索引: 索引端点每次应答 4.74 MB, 逐来源检测与逐站点重试都付不起这个体积; SKU 端点与
        索引同 host 同 API, 一次小请求即可回答「此刻能否连通」.
        """
        return await probe_get(
            self.client.web_client,
            self._sku_url(_PROBE_SKU),
            cookies=self.cookies,
            headers=self.headers,
        )

    def _sku_url(self, sku_id: str) -> str:
        return f"{self.base_url}/api/sku/item/{sku_id}"

    def _product_url(self, product_uuid: str) -> str:
        return f"{self.base_url}/api/product/{product_uuid}"

    def _sitemap_url(self) -> str:
        return f"{self.base_url}/api/sku/sitemap"

    def _search_url(self, number: str) -> str:
        return f"{self.base_url}/api/search?isEnabledQuery=true&searchText={quote(number)}&isEnableAggregation=false"

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """索引 → 直连候选 → 搜索兜底. 三档皆未命中返回 ``None``: 本店没有该番号不是来源失败.

        每一档都按剥离前缀后的番号核对, 站点把相近番号排在前面也不会被当成命中.
        """
        number = query.number.upper()
        if not number:
            return None

        for via, lookup in (("index", self._from_index), ("direct", self._from_direct), ("search", self._from_search)):
            hit = await lookup(number)
            if hit is not None:
                self.logger.debug(
                    "search hit", number=number, sku_id=hit.sku_id, product_uuid=hit.product_uuid, via=via
                )
                return self._product_url(hit.product_uuid)

        self.logger.debug("search miss", number=number)
        return None

    async def _from_index(self, number: str) -> SkuHit | None:
        index = await self._ensure_index()
        return None if index is None else self._pick(index.candidates(number), number)

    async def _from_direct(self, number: str) -> SkuHit | None:
        """裸番号与 GOOE 特典前缀的直连候选.

        404 是「本店没有这个 SKU」, 因此换下一个候选; 全部 404 时返回 ``None`` 记未命中, 不抛出 ——
        否则一个不售卖的番号会被记成来源失败.
        """
        number_no_dash = number.replace("-", "")
        for sku_id in (number, number_no_dash, f"GOOE{number}", f"GOOE{number_no_dash}"):
            try:
                data = await self.client.get_json(self._sku_url(sku_id))
            except RequestError as exc:
                if exc.reason is FailureReason.NOT_FOUND:
                    continue
                raise
            hit = _hit_from_sku(sku_id, data)
            if hit is not None:
                picked = self._pick([hit], number)
                if picked is not None:
                    return picked
        return None

    async def _from_search(self, number: str) -> SkuHit | None:
        """搜索兜底. 站点按模糊匹配返回且排序不保证精确项在前, 因此只按 ``number_match`` 判定, 不取首条.

        应答不合读模型时按「这一档不可用」处理, 不让索引与直连都已经落空时再被一个坏应答记成来源失败.
        """
        try:
            payload = await self.client.get_json(self._search_url(number))
        except RequestError as exc:
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        hits = _parse_search_hits(payload)
        if hits is None:
            self.logger.warning("search response does not match the read model", number=number)
            return None
        return self._pick(hits, number)

    def _pick(self, hits: Iterable[SkuHit], number: str) -> SkuHit | None:
        """按取舍键排序后用 ``select_number_index`` 核验剥离前缀后的番号; 确认不了的一律视为未命中.

        候选先按取舍键排序, 因此同属一部时命中的是裸番号 SKU (或前缀序最靠前的那个).
        """
        ordered = sorted(hits, key=lambda hit: rank_sku(hit.sku_id))
        index = select_number_index([hit.number for hit in ordered], number)
        return ordered[index] if index is not None else None

    async def _ensure_index(self) -> SitemapIndex | None:
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

    def _fresh_index(self) -> SitemapIndex | None:
        """未过期的索引; 尚未拉取或已过期时为 ``None``."""
        if self._index is None or time.time() - self._index_at >= _INDEX_TTL_S:
            return None
        return self._index

    async def _refresh_index(self) -> SitemapIndex | None:
        try:
            payload = await self.client.get_bytes(self._sitemap_url())
            index = await asyncio.to_thread(parse_sitemap, payload)
        except (RequestError, SitemapUnusable) as exc:
            self._retry_at = time.time() + _INDEX_RETRY_S
            # 刷新失败沿用旧索引, 不清空: 索引端点不可用不能让整个来源失效, 后两档检索仍可命中.
            self.logger.warning("sku index refresh failed", error=str(exc), has_stale=self._index is not None)
            return self._index
        self._index, self._index_at, self._retry_at = index, time.time(), 0.0
        self.logger.info("sku index ready", numbers=len(index.by_number))
        return index

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        data = await self.client.get_json(url, headers={"Accept": "application/json"})
        if not data or not isinstance(data, dict):
            return None

        # 番号取同捆特典 SKU 与包装 SKU 中优先级最高的那个, 剥离前缀后即原番号; 与检索同一套取舍规则.
        sku_list: list[dict[str, Any]] = data.get("sku") or []
        number = preferred_number(str(sku.get("deliveryItemId", "")) for sku in sku_list)
        if not number:
            return None

        title = data.get("title")
        release = data.get("mgsStartAt") or data.get("createdAt")
        if release and "T" in release:
            release = release.split("T")[0]

        runtime = data.get("playTime")

        actors: list[str] = []
        for a in data.get("actress") or []:
            name = a.get("name", "") if isinstance(a, dict) else str(a)
            if name:
                actors.append(name)

        directors: list[str] = []
        for d in data.get("directors") or []:
            name = d.get("name", "") if isinstance(d, dict) else str(d)
            if name:
                directors.append(name)

        tags: list[str] = []
        for g in data.get("genre") or []:
            name = g.get("name", "") if isinstance(g, dict) else str(g)
            if name:
                tags.append(name)

        # maker/label/series 可能是 dict 或 list.
        def _extract_name(value: Any) -> str | None:
            if isinstance(value, dict):
                return value.get("name") or None
            if isinstance(value, list) and value:
                v0 = value[0]
                return v0.get("name") if isinstance(v0, dict) else str(v0)
            if isinstance(value, str):
                return value
            return None

        studio = "プレステージ"
        maker_name = _extract_name(data.get("maker"))
        label_name = _extract_name(data.get("label"))
        series_name = _extract_name(data.get("series"))

        def _image_url(path: str | None) -> str | None:
            # 图片在站点自身的 /api/media/ 下: 新片为 {x}/{y}/{uuid}.jpg, 老片为 goods/prestige/...
            if not path:
                return None
            return f"{self.base_url}/api/media/{path}" if not path.startswith("http") else path

        # thumbnail 是竖版前封, packageImage 是横版整幅; 槽位按朝向取 (poster 竖版 / thumb 横版), 故两字段交叉使用.
        front_path = (data.get("thumbnail") or {}).get("path") if isinstance(data.get("thumbnail"), dict) else None
        spread_path = (
            (data.get("packageImage") or {}).get("path") if isinstance(data.get("packageImage"), dict) else None
        )
        poster_url = _image_url(front_path)
        thumb_url = _image_url(spread_path)

        extrafanart: list[str] = []
        for img in data.get("media") or []:
            path = img.get("path") if isinstance(img, dict) else None
            img_url = _image_url(path)
            if img_url:
                extrafanart.append(img_url)

        external_id = data.get("uuid", "")

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio,
            publisher=label_name or maker_name or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series_name or None,
            plot=data.get("body") or None,
            poster_urls=[poster_url] if poster_url else [],
            thumb_urls=[thumb_url] if thumb_url else [],
            directors=directors,
            extrafanart=extrafanart,
            source_url=url,
            external_id=external_id or None,
        )
