"""works 主题厂牌站 (faleno / dahlia) 的解析规则与非法输入.

用例只覆盖 fixture 无法表达的规则: 番号形态、出演女优分隔、字段缺省与载荷异常.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.sites.dahlia import DahliaCrawler
from amane.crawlers.sites.faleno import FalenoCrawler
from amane.crawlers.sites.wp_works import (
    WordPressWorksCrawler,
    number_from_slug,
    parse_minutes,
    slugify,
    split_actors,
    strip_actor_suffix,
)
from amane.net.errors import FailureReason, SourceError


def _crawler(cls: type[WordPressWorksCrawler], payload: Any) -> WordPressWorksCrawler:
    web = AsyncMock()
    web.get_json.return_value = payload
    return cls(client=HttpClient(web=web))


@pytest.mark.parametrize(
    ("number", "slug"),
    [
        ("FNS-257", "fns257"),
        ("fns257", "fns257"),
        ("FNS257", "fns257"),
        ("MGOLD_064", "mgold064"),
        ("FNS-241SP", "fns241sp"),
        ("", ""),
        ("---", ""),
    ],
)
def test_slugify(number: str, slug: str) -> None:
    assert slugify(number) == slug


@pytest.mark.parametrize(
    ("slug", "number"),
    [
        ("fns257", "FNS-257"),
        ("fns001", "FNS-001"),
        ("fns241sp", "FNS-241SP"),
        ("mgold064", "MGOLD-064"),
        ("dldss529", "DLDSS-529"),
        ("257", "257"),
        ("fns", "FNS"),
    ],
)
def test_number_from_slug(slug: str, number: str) -> None:
    assert number_from_slug(slug) == number


@pytest.mark.parametrize(
    ("value", "actors"),
    [
        ("明里つむぎ", ["明里つむぎ"]),
        ("吉高寧々/本田もも/沙月恵奈", ["吉高寧々", "本田もも", "沙月恵奈"]),
        ("八蜜凛 女神ジュン", ["八蜜凛", "女神ジュン"]),
        ("天使もえ、桃尻かなめ", ["天使もえ", "桃尻かなめ"]),
        ("", []),
        ("  ", []),
        ("/ /", []),
        (None, []),
    ],
)
def test_split_actors(value: str | None, actors: list[str]) -> None:
    assert split_actors(value) == actors


@pytest.mark.parametrize(
    ("title", "actors", "expected"),
    [
        ("衝撃移籍 明里つむぎ", ["明里つむぎ"], "衝撃移籍"),
        ("衝撃移籍　明里つむぎ", ["明里つむぎ"], "衝撃移籍"),
        ("夢見るふたご丼 どっちもシャブっても", ["黒澤ななみ"], "夢見るふたご丼 どっちもシャブっても"),
        ("明里つむぎ", ["明里つむぎ"], ""),
        ("衝撃移籍", [], "衝撃移籍"),
    ],
)
def test_strip_actor_suffix(title: str, actors: list[str], expected: str) -> None:
    assert strip_actor_suffix(title, actors) == expected


@pytest.mark.parametrize(
    ("value", "minutes"),
    [("120分", 120), ("97分", 97), ("", None), (None, None), ("分", None)],
)
def test_parse_minutes(value: str | None, minutes: int | None) -> None:
    assert parse_minutes(value) == minutes


@pytest.mark.parametrize(
    ("slug", "publisher"),
    [
        ("fns257", "FALENOstar"),
        ("fcdss090", "FALENOstar"),
        ("mgold064", "maryGOLD"),
        ("jimmy011", "JimmyScandal"),
    ],
)
def test_faleno_publisher(slug: str, publisher: str) -> None:
    crawler = _crawler(FalenoCrawler, [])
    assert crawler._publisher(slug) == publisher


def test_dahlia_publisher_is_single_label() -> None:
    crawler = _crawler(DahliaCrawler, [])
    assert crawler._publisher("dldss529") == "DAHLIA"


@pytest.mark.asyncio
async def test_scrape_returns_none_without_post() -> None:
    crawler = _crawler(FalenoCrawler, [])
    assert await crawler._scrape("https://faleno.jp/top/wp-json/wp/v2/works?slug=fns257") is None


@pytest.mark.asyncio
async def test_scrape_tolerates_missing_acf_and_null_gallery() -> None:
    payload = [
        {
            "slug": "fns257",
            "acf_custom_images": {"gallery_urls": ["https://cdn.example/1.jpg", None, ""]},
        }
    ]
    crawler = _crawler(FalenoCrawler, payload)
    result = await crawler._scrape("https://faleno.jp/top/wp-json/wp/v2/works?slug=fns257")

    assert result is not None
    assert result.number == "FNS-257"
    assert result.title is None
    assert result.actors == []
    assert result.extrafanart == ["https://cdn.example/1.jpg"]
    assert result.studio == "FALENO"
    assert result.publisher == "FALENOstar"
    assert result.source_url == "https://faleno.jp/top/works/fns257/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{"code": "rest_no_route"}, "forbidden", [{"slug": 257}], [None]],
)
async def test_scrape_reports_parse_error(payload: Any) -> None:
    crawler = _crawler(FalenoCrawler, payload)
    with pytest.raises(SourceError) as excinfo:
        await crawler._scrape("https://faleno.jp/top/wp-json/wp/v2/works?slug=fns257")
    assert excinfo.value.reason is FailureReason.PARSE_ERROR
