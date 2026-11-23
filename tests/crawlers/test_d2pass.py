"""d2pass 家族共用实现的几条判据, 数据驱动覆盖不到的部分.

TOML 用例是「输入 → 输出」解析, 覆盖正常条目、早年缺档的预告片降级与站点自报番号不符;
HTTP 状态码、请求次数与「未命中时是否发过请求」不在其表达能力内, 因此在这里断言.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.d2pass import OnePondoCrawler, TenMusumeCrawler
from amane.net.errors import FailureKind, RequestError, RequestFailure, SourceError

URL = "https://www.10musume.com/dyn/phpauto/movie_details/movie_id/112226_01.json"
Route = Callable[[str], object]


def _payload(movie_id: str = "112226_01") -> dict[str, object]:
    return {"MovieID": movie_id, "Title": "タイトル", "Release": "2026-11-22", "Duration": 3600}


def _raises(status: int) -> Route:
    def route(url: str) -> object:
        raise RequestError(url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}"))

    return route


def _crawler(route: Route) -> TenMusumeCrawler:
    web = AsyncMock()

    async def get_json(url: str, **kwargs: object) -> object:
        return route(url)

    web.get_json.side_effect = get_json
    return TenMusumeCrawler(client=HttpClient(web=web))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "expected_reason"),
    [
        # 未知番号: 站点 404, 与「没有这部片」同义.
        (_raises(404), None),
        # 服务端错误与限速照旧上报, 不能静默成未命中.
        (_raises(500), "server_error"),
        (_raises(429), "rate_limited"),
    ],
)
async def test_http_status_maps_to_miss_or_failure(route: Route, expected_reason: str | None) -> None:
    crawler = _crawler(route)

    if expected_reason is None:
        assert await crawler.fetch(SearchQuery(number="112226_01")) is None
        return
    with pytest.raises(SourceError) as excinfo:
        await crawler.fetch(SearchQuery(number="112226_01"))
    assert excinfo.value.reason == expected_reason


@pytest.mark.asyncio
async def test_hit_requests_detail_json_once() -> None:
    """一次抓取只发一次请求: 基类的 _search → _scrape 会对同一份 JSON 取两次."""
    web = AsyncMock()

    async def get_json(url: str, **kwargs: object) -> object:
        return _payload()

    web.get_json.side_effect = get_json
    crawler = TenMusumeCrawler(client=HttpClient(web=web))

    assert await crawler.fetch(SearchQuery(number="112226_01")) is not None
    assert web.get_json.await_count == 1
    assert web.get_json.await_args.args == (URL,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crawler_cls", "number", "accepted"),
    [
        # 序号位数决定归属: 两位属天然むすめ, 三位属一本道, 短横线属カリビアンコム.
        (TenMusumeCrawler, "112226_01", True),
        (TenMusumeCrawler, "112226_001", False),
        (OnePondoCrawler, "112226_001", True),
        (OnePondoCrawler, "112226_01", False),
        (OnePondoCrawler, "112226-001", False),
        (OnePondoCrawler, "SSIS-497", False),
        (OnePondoCrawler, "", False),
    ],
)
async def test_number_shape_gate_sends_no_request(
    crawler_cls: type[TenMusumeCrawler] | type[OnePondoCrawler], number: str, accepted: bool
) -> None:
    """形态不符时不发请求; TOML 只能断言返回 None, 断言不了「没发出去」."""
    web = AsyncMock()
    crawler = crawler_cls(client=HttpClient(web=web))

    url = await crawler._search(SearchQuery(number=number))

    if not accepted:
        assert url is None
        web.get_json.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crawler_cls", "number", "poster"),
    [
        (
            OnePondoCrawler,
            "112226_001",
            "https://www.1pondo.tv/dyn/dla/images/movies/112226_001/jacket/jacket.jpg",
        ),
        (
            TenMusumeCrawler,
            "112226_01",
            "https://www.10musume.com/moviepages/112226_01/images/jacket.jpg",
        ),
    ],
)
async def test_poster_is_derived_before_detail_thumb(
    crawler_cls: type[TenMusumeCrawler] | type[OnePondoCrawler], number: str, poster: str
) -> None:
    """原生竖版海报排在详情缩略之前: 候选是同一张图的回退链."""
    web = AsyncMock()

    async def get_json(url: str, **kwargs: object) -> object:
        return {**_payload(movie_id=number), "MovieThumb": "https://example.test/thum.jpg"}

    web.get_json.side_effect = get_json
    crawler = crawler_cls(client=HttpClient(web=web))

    meta = await crawler.fetch(SearchQuery(number=number))

    assert meta is not None
    assert meta.poster_urls == [poster, "https://example.test/thum.jpg"]


@pytest.mark.asyncio
async def test_gallery_contributes_public_stills_only() -> None:
    """详情声明 Gallery: true 时才取剧照; 会员样本不取."""
    web = AsyncMock()
    gallery_url = "https://www.10musume.com/dyn/dla/json/movie_gallery/112226_01.json"

    async def get_json(url: str, **kwargs: object) -> object:
        if url == gallery_url:
            return {
                "MovieID": "112226_01",
                "Rows": [
                    {"Img": "movie_gallery/sample/112226_01/a.jpg", "Protected": False},
                    {"Img": "movie_gallery/member/112226_01/b.jpg", "Protected": True},
                ],
            }
        return {**_payload(), "Gallery": True}

    web.get_json.side_effect = get_json
    crawler = TenMusumeCrawler(client=HttpClient(web=web))

    meta = await crawler.fetch(SearchQuery(number="112226_01"))

    assert meta is not None
    assert meta.extrafanart == ["https://www.10musume.com/dyn/dla/images/movie_gallery/sample/112226_01/a.jpg"]
    assert web.get_json.await_count == 2


@pytest.mark.asyncio
async def test_gallery_failure_keeps_metadata() -> None:
    """剧照是附加资源: 取剧照失败不把整条刮削变成失败."""
    web = AsyncMock()

    async def get_json(url: str, **kwargs: object) -> object:
        if "movie_gallery" in url:
            raise RequestError(url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=500, message="HTTP 500"))
        return {**_payload(), "Gallery": True}

    web.get_json.side_effect = get_json
    crawler = TenMusumeCrawler(client=HttpClient(web=web))

    meta = await crawler.fetch(SearchQuery(number="112226_01"))

    assert meta is not None
    assert meta.extrafanart == []
