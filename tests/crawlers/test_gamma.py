"""Gamma 平台爬虫单元测试: 演员集合消歧、画质降级与凭据缺失的守卫."""

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import FilmActor, SearchQuery
from amane.crawlers.sites.gamma import GammaCrawler, _scene_url, _trailer_urls, pick_scene

_TRAILERS = {
    "160p": "https://t/tr_lw.mp4",
    "360p": "https://t/tr_med.mp4",
    "720p": "https://t/tr_720p.mp4",
}


def _hit(clip_id: int, actors: list[str]) -> dict:
    return {
        "clip_id": clip_id,
        "title": "OMG I Fucked My Bratty Student",
        "url_title": "OMG-I-Fucked-My-Bratty-Student",
        "sitename": "givemeteens",
        "trailers": dict(_TRAILERS),
        "actors": [{"name": name} for name in actors],
    }


@pytest.mark.parametrize(
    ("label", "hits", "actors", "expected"),
    [
        # 同名系列的不同演员各自是独立条目, 只能按演员集合确认.
        ("演员集合命中", [_hit(2, ["A", "B"]), _hit(1, ["C", "D"])], ["C", "D"], 1),
        ("演员顺序无关", [_hit(1, ["D", "C"])], ["C", "D"], 1),
        ("演员不匹配", [_hit(1, ["A", "B"])], ["C", "D"], None),
        # 演员是空的就不能确认, 不回退首条.
        ("无演员", [_hit(1, ["A"])], [], None),
        ("空结果", [], ["A"], None),
        ("结构不符", "not a list", ["A"], None),
    ],
)
def test_pick_scene(label: str, hits: object, actors: list[str], expected: int | None) -> None:
    scene = pick_scene(hits, actors)
    if expected is None:
        assert scene is None, label
    else:
        assert scene is not None, label
        assert scene["clip_id"] == expected, label


@pytest.mark.parametrize(
    ("label", "trailers", "expected"),
    [
        # 按画质降级: 有 1080p 就用 1080p.
        ("取最高档", {**_TRAILERS, "1080p": "https://t/tr_1080p.mp4"}, ["https://t/tr_1080p.mp4"]),
        ("没有 1080p 取 720p", _TRAILERS, ["https://t/tr_720p.mp4"]),
        ("只有低档", {"160p": "https://t/lw.mp4"}, ["https://t/lw.mp4"]),
        ("空", {}, []),
        ("结构不符", None, []),
    ],
)
def test_trailer_urls(label: str, trailers: object, expected: list[str]) -> None:
    assert _trailer_urls(trailers) == expected, label


def test_scene_url_requires_all_parts() -> None:
    scene = {"sitename": "givemeteens", "url_title": "Title", "clip_id": 209593}

    assert _scene_url(scene) == "https://www.famedigital.com/en/video/givemeteens/Title/209593"
    assert _scene_url({"sitename": "givemeteens", "url_title": "Title"}) is None


def _crawler(home: str | None, payload: object) -> GammaCrawler:
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        if home is None:
            raise RuntimeError("offline")
        return home

    web.get_text.side_effect = get_text
    web.post_json.return_value = payload
    return GammaCrawler(client=HttpClient(web=web))


def _query(actors: list[str] | None = None, title: str = "T") -> SearchQuery:
    from amane.aggregate.models import AggregatedMetadata

    partial = AggregatedMetadata(number="x", title=title, actors=[FilmActor(name=name) for name in (actors or [])])
    return SearchQuery(number="x", partial_result=partial)


_HOME = '<script>window.env={"api":{"algolia":{"applicationID":"APP","apiKey":"KEY"}}}</script>'


@pytest.mark.asyncio
async def test_fetch_builds_trailer() -> None:
    payload = {"hits": [_hit(1, ["A", "B"])]}
    meta = await _crawler(_HOME, payload).fetch(_query(["A", "B"], "OMG"))

    assert meta is not None
    assert meta.trailer_urls == ["https://t/tr_720p.mp4"]
    assert meta.number == "1"


@pytest.mark.asyncio
async def test_fetch_without_partial_returns_none() -> None:
    crawler = _crawler(_HOME, {"hits": []})

    assert await crawler.fetch(SearchQuery(number="x")) is None


@pytest.mark.asyncio
async def test_fetch_without_credentials_returns_none() -> None:
    crawler = _crawler("<html>no creds</html>", {"hits": [_hit(1, ["A"])]})

    assert await crawler.fetch(_query(["A"])) is None


@pytest.mark.asyncio
async def test_fetch_offline_returns_none() -> None:
    crawler = _crawler(None, {"hits": []})

    assert await crawler.fetch(_query(["A"])) is None
