"""prestige 爬虫单元测试: 「输入 → 输出」在 ``cases/prestige/``, 这里只测时序, 规则边界与降级."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, cast

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.prestige import (
    PrestigeCrawler,
    SitemapUnusable,
    SkuPrefix,
    parse_sitemap,
    preferred_number,
    rank_sku,
    strip_sku_prefix,
)
from amane.net.errors import FailureKind, RequestError, RequestFailure

from .driven import CASES_DIR, load_binary_response_file

if TYPE_CHECKING:
    from amane.net.http import WebClient

_BASE = "https://www.prestige-av.com"


def _product(uuid: str) -> str:
    return f"{_BASE}/api/product/{uuid}"


def _sitemap_payload() -> bytes:
    return load_binary_response_file(CASES_DIR / "prestige", "sitemap_subset.json.gz")


def _index_down() -> RequestError:
    return RequestError(f"{_BASE}/api/sku/sitemap", RequestFailure(kind=FailureKind.CURL, message="boom"))


def _direct_hit(uuid: str = "uuid-direct") -> dict[str, object]:
    return {"deliveryItemId": "ABW-125", "parentProduct": {"uuid": uuid}}


def _search_hits(*sku_ids: str) -> dict[str, object]:
    """``/api/search`` 的最小应答, 候选顺序即站点给出的顺序 (不保证精确项在前)."""
    sources = [{"deliveryItemId": sku, "productUuid": f"uuid-{i}"} for i, sku in enumerate(sku_ids)]
    return {"hits": {"hits": [{"_source": source} for source in sources]}}


class _FakeWeb:
    """假 WebClient: 索引正文 + 按地址片段匹配的 JSON 应答 + 请求计数.

    不复用 ``driven.build_mock`: 那边按夹具文件装配且每条路由恒定应答, 这里要内联的最小应答与调用次数
    (退避与并发判定靠它), 套过去得先把内联应答落盘.
    """

    def __init__(self, sitemap: bytes | Exception, json_routes: dict[str, object | Exception] | None = None) -> None:
        self._sitemap = sitemap
        self._json_routes = dict(json_routes or {})
        self.calls = 0
        self.sitemap_calls = 0

    async def get_bytes(self, url: str, **kwargs: object) -> bytes:
        self.calls += 1
        self.sitemap_calls += 1
        if isinstance(self._sitemap, Exception):
            raise self._sitemap
        return self._sitemap

    async def get_json(self, url: str, **kwargs: object) -> object:
        self.calls += 1
        for pattern, payload in self._json_routes.items():
            if pattern in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        # 站点对不存在的 SKU 回 404; 未声明的地址按同一口径处理, 检索因此落到下一档.
        failure = RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404")
        raise RequestError(url, failure)


def _crawler(web: _FakeWeb) -> PrestigeCrawler:
    return PrestigeCrawler(HttpClient(web=cast("WebClient", web), browser=None))


# 前缀后接分隔符时它是站内番号本身: 按 GOO 前缀剥离会得到 D-002 / L-017 / -155, 全站有这类独立系列.
# 正向剥离与「裸番号优先」由 TOML 用例覆盖, 这里只留它表达不了的边界.
@pytest.mark.parametrize(
    ("sku_id", "expected"),
    [
        ("GOOE-155", "GOOE-155"),
        ("GOOD-002", "GOOD-002"),
        ("GOOL-017", "GOOL-017"),
        ("XTKTABP-546", "XTKTABP-546"),
        ("tktabp-546", "tktabp-546"),
        ("", ""),
    ],
)
def test_strip_sku_prefix_keeps_non_prefixed_ids(sku_id: str, expected: str) -> None:
    assert strip_sku_prefix(sku_id) == expected


# 无裸番号时按声明顺序: TKT 在 GOOE 之前, 尽管 GOOE 的写法更靠前. 「裸番号优先」由 TOML 用例覆盖.
@pytest.mark.parametrize(
    ("sku_ids", "expected"),
    [
        (["GOOEABF-001", "TKTABF-001"], "ABF-001"),
        (["TKTABP-546"], "ABP-546"),
        ([], None),
        (["", ""], None),
    ],
)
def test_preferred_number(sku_ids: list[str], expected: str | None) -> None:
    assert preferred_number(sku_ids) == expected
    # 取舍键是单一真源 (检索归并与详情番号共用), 在全部七个前缀上单调: 裸番号在前, 其后按声明顺序.
    shuffled = [*(f"{prefix.value}ABF-084" for prefix in reversed(list(SkuPrefix))), "ABF-084"]
    assert sorted(shuffled, key=rank_sku) == ["ABF-084", *(f"{prefix.value}ABF-084" for prefix in SkuPrefix)]


def test_parse_sitemap_folds_index_keys() -> None:
    """索引键与 number_match 同一口径折叠 (大小写与短横线), 未收录的番号是空结果."""
    index = parse_sitemap(_sitemap_payload())
    assert index.candidates("bgn066") == index.candidates("BGN-066")
    assert index.candidates("DSVR-0122") == ()


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"{}",  # 对象而不是数组
        b'[{"deliveryItemId": "ABW-125"}]',  # 缺 productUuid
        b'[{"productUuid": "u"}]',  # 缺 deliveryItemId
        b"[]",  # 空索引: 上游改版或异常应答, 不能当成「站点上什么都没有」
    ],
)
def test_parse_sitemap_rejects_unusable_payload(payload: bytes) -> None:
    with pytest.raises(SitemapUnusable):
        parse_sitemap(payload)


@pytest.mark.asyncio
async def test_search_fallback_only_accepts_verified_numbers() -> None:
    """搜索只按番号判定: 相近番号排在首位不命中; 应答不合读模型时按这一档不可用, 都不记来源失败."""
    near_miss = _crawler(_FakeWeb(_index_down(), {"api/search": _search_hits("ABW-1251", "TKTABW-1251")}))
    assert await near_miss._search(SearchQuery(number="ABW-1250")) is None

    found = _crawler(_FakeWeb(_index_down(), {"api/search": _search_hits("TKTABW-125", "ABW-125")}))
    assert await found._search(SearchQuery(number="ABW-125")) == _product("uuid-1")

    broken = _crawler(_FakeWeb(_index_down(), {"api/search": {"unexpected": "shape"}}))
    assert await broken._search(SearchQuery(number="ABW-125")) is None


@pytest.mark.asyncio
async def test_unexpected_direct_failure_still_propagates() -> None:
    """与「不存在」分开: 5xx 等失败仍冒泡成来源失败, 不能被吞成未命中."""
    failure = RequestFailure(kind=FailureKind.HTTP_STATUS, status=503, message="HTTP 503")
    crawler = _crawler(_FakeWeb(_index_down(), {"api/sku/item/": RequestError(_BASE, failure)}))

    with pytest.raises(RequestError):
        await crawler._search(SearchQuery(number="ABW-125"))


@pytest.mark.asyncio
async def test_unexpected_sitemap_shape_degrades_to_direct() -> None:
    """索引正文不合读模型时降级到直连, 不记来源失败, 也不把坏正文留在实例上."""
    crawler = _crawler(_FakeWeb(b"<html>maintenance</html>", {"api/sku/item/": _direct_hit()}))
    assert await crawler._search(SearchQuery(number="ABW-125")) == _product("uuid-direct")
    assert crawler._index is None


@pytest.mark.asyncio
async def test_index_backoff_skips_refetch_until_due() -> None:
    """索引失败后进入退避: 退避期内不再请求索引端点, 避免每次刮削各付一次超时."""
    web = _FakeWeb(_index_down(), {"api/sku/item/": _direct_hit("uuid-1")})
    crawler = _crawler(web)
    await asyncio.gather(*(crawler._search(SearchQuery(number="ABW-125")) for _ in range(3)))
    assert web.sitemap_calls == 1


@pytest.mark.asyncio
async def test_stale_index_is_kept_when_refresh_fails() -> None:
    """刷新失败沿用旧索引, 不清空: 索引端点挂了不能让已建立的检索能力一起失效."""
    crawler = _crawler(_FakeWeb(_index_down()))
    crawler._index = parse_sitemap(_sitemap_payload())
    crawler._index_at = time.time() - 2 * 86_400  # 已过期, 检索会触发刷新

    # 索引端点已被打成失败, 仍能给出索引里的商品: 旧索引确实被保留了.
    assert await crawler._search(SearchQuery(number="BGN-066")) == _product("838d1f17-9743-4a9d-a993-6d54fd07f0af")


@pytest.mark.asyncio
async def test_concurrent_searches_fetch_the_index_once() -> None:
    web = _FakeWeb(_sitemap_payload())
    crawler = _crawler(web)
    await asyncio.gather(*(crawler._search(SearchQuery(number="BGN-066")) for _ in range(5)))
    assert web.sitemap_calls == 1


def test_empty_number_is_a_miss() -> None:
    web = _FakeWeb(_sitemap_payload())
    assert asyncio.run(_crawler(web)._search(SearchQuery(number=""))) is None
    assert web.calls == 0
