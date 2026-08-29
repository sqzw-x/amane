"""ThePornDB 演员爬虫: 映射 / 精确匹配 / 无 token 空跑."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.actor.sites.theporndb import (
    ThePornDBActorCrawler,
    performer_to_metadata,
    pick_performer,
)
from amane.crawlers.http import HttpClient
from amane.enums import ActorGender

_PID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _perf(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": _PID,
        "name": "Angela White",
        "aliases": ["Angela"],
        "gender": "FEMALE",
        "birth_date": "1985-03-04",
        "country": "AU",
        "height": 160,
        "measurements": {"cup_size": "G", "band_size": 86, "waist": 66, "hip": 96},
        "images": [{"url": "https://cdn.example/a.webp"}],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("perf", "want"),
    [
        (
            _perf(),
            {
                "name": "Angela White",
                "aliases": ["Angela"],
                "gender": ActorGender.FEMALE,
                "birthday": "1985-03-04",
                "birthplace": "AU",
                "height": 160,
                "bust": 86,
                "waist": 66,
                "hip": 96,
                "cup": "G",
                "image_urls": ["https://cdn.example/a.webp"],
                "provider_ids": {"theporndb": _PID},
                "source_url": f"https://theporndb.net/performers/{_PID}",
            },
        ),
        pytest.param(_perf(name="  ", aliases=[]), None, id="blank-name"),
        pytest.param(_perf(name=None, aliases=[]), None, id="missing-name"),
        pytest.param(
            _perf(gender="MALE", aliases=[]),
            {"gender": ActorGender.MALE},
            id="male",
        ),
        pytest.param(_perf(gender="TRANSGENDER_FEMALE"), {"gender": None}, id="unmapped-gender"),
        pytest.param(_perf(gender="INTERSEX"), {"gender": None}, id="intersex"),
        pytest.param(_perf(height=0), {"height": None}, id="zero-height"),
        pytest.param(_perf(height=-1), {"height": None}, id="negative-height"),
        pytest.param(_perf(height=True), {"height": None}, id="bool-height"),
        pytest.param(
            _perf(measurements={"cup_size": "", "band_size": 0, "waist": None, "hip": -3}),
            {"cup": None, "bust": None, "waist": None, "hip": None},
            id="empty-measurements",
        ),
        pytest.param(_perf(measurements=None), {"bust": None, "cup": None}, id="null-measurements"),
        pytest.param(_perf(birth_date="not-a-date"), {"birthday": None}, id="invalid-birthday"),
        pytest.param(_perf(birth_date=1985), {"birthday": None}, id="non-string-birthday"),
        pytest.param(
            _perf(aliases=["Angela White", "Angela", "Angie", "Angela"]),
            {"aliases": ["Angela", "Angie"]},
            id="alias-dedupe-and-drop-canonical",
        ),
        pytest.param(_perf(id=""), {"provider_ids": {}, "source_url": None}, id="missing-id"),
        pytest.param(
            _perf(images=[{"url": ""}, {"url": "https://cdn.example/b.webp"}, "x", {}]),
            {"image_urls": ["https://cdn.example/b.webp"]},
            id="image-skip-empty",
        ),
        pytest.param(_perf(images="nope"), {"image_urls": []}, id="images-not-list"),
        pytest.param(_perf(aliases="Angela"), {"aliases": []}, id="aliases-not-list"),
    ],
)
def test_performer_to_metadata(perf: dict[str, Any], want: dict[str, Any] | None) -> None:
    got = performer_to_metadata(perf)
    if want is None:
        assert got is None
        return
    assert got is not None
    for field, expected in want.items():
        assert getattr(got, field) == expected, field


@pytest.mark.parametrize(
    ("results", "name", "want_id"),
    [
        ([_perf(), _perf(id="other", name="Angela Yee")], "Angela White", _PID),
        ([_perf(), _perf(id="other", name="Angela Yee")], "angela white", _PID),
        ([_perf(), _perf(id="other", name="Angela Yee")], "Angela", _PID),
        ([_perf(name="Angela Yee", aliases=[])], "Angela", None),
        ([], "Angela White", None),
        (["x", None, _perf()], "Angela White", _PID),
        ([_perf(name="Angela  White", aliases=[])], "Angela White", None),
        ([_perf()], "", None),
        ([_perf()], "   ", None),
    ],
)
def test_pick_performer(results: list[Any], name: str, want_id: str | None) -> None:
    hit = pick_performer(results, name)
    if want_id is None:
        assert hit is None
    else:
        assert hit is not None
        assert hit["id"] == want_id


def test_pick_performer_nfkc_fullwidth() -> None:
    # NFKC: 全角空格 / 全角字母折到 ASCII 后再 casefold.
    hit = pick_performer([_perf()], "Ａngela White")
    assert hit is not None
    assert hit["id"] == _PID


def _crawler(*, token: str | None = "test-token") -> tuple[ThePornDBActorCrawler, AsyncMock]:
    mock_web = AsyncMock()
    config = SiteConfig(api_token=token) if token is not None else None
    crawler = ThePornDBActorCrawler(client=HttpClient(web=mock_web), config=config)
    return crawler, mock_web


@pytest.mark.asyncio
async def test_fetch_without_token_skips_http() -> None:
    crawler, mock_web = _crawler(token=None)
    assert await crawler.fetch("Angela White") is None
    mock_web.post_json.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_empty_token_skips_http() -> None:
    crawler, mock_web = _crawler(token="")
    assert await crawler.fetch("Angela White") is None
    mock_web.post_json.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_blank_name_skips_http() -> None:
    crawler, mock_web = _crawler()
    assert await crawler.fetch("  ") is None
    mock_web.post_json.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_hit_sends_bearer_and_maps() -> None:
    crawler, mock_web = _crawler()
    mock_web.post_json.return_value = {"data": {"searchPerformer": [_perf()]}}
    meta = await crawler.fetch("Angela White")
    assert meta is not None
    assert meta.name == "Angela White"
    assert meta.provider_ids == {"theporndb": _PID}
    mock_web.post_json.assert_called_once()
    args, kwargs = mock_web.post_json.call_args
    assert args[0] == "https://theporndb.net/graphql"
    assert kwargs["headers"] == {"Authorization": "Bearer test-token"}
    assert kwargs["json"]["variables"] == {"term": "Angela White"}
    assert "searchPerformer" in kwargs["json"]["query"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"searchPerformer": []}},
        {"data": {"searchPerformer": [_perf(name="Angela Yee")]}},
        {"data": {"searchPerformer": None}},
        {"data": {}},
        {"data": None},
        {},
        None,
        {"data": {"searchPerformer": "oops"}},
        {"data": []},
    ],
)
async def test_fetch_miss_or_malformed(payload: object) -> None:
    crawler, mock_web = _crawler()
    mock_web.post_json.return_value = payload
    assert await crawler.fetch("Angela White") is None
    mock_web.post_json.assert_called_once()
