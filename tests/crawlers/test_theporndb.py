"""theporndb 爬虫单元测试: 检索命中判据与应答投影.

应答按 TPDB 上的真实字段值构造: ``code`` 是 kin8 站内 4 位 ``movie_id``, 他厂会用同一个数字当番号.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.theporndb import ThePornDBCrawler, pick_scene
from amane.parsing import ContentType

_TOKEN = "test-token"


def _scene(code: str, *, title: str, date: str, studio: str, scene_id: str = "scene-1") -> dict[str, Any]:
    return {"id": scene_id, "code": code, "title": title, "date": date, "studio": {"name": studio}}


_KIN8_CUTIE = _scene(
    "0236",
    title="Kin8 Cutie",
    date="2010-05-27",
    studio="Kin8tengoku",
    scene_id="96842b9f-0405-481b-a44a-1c7fb5b0370c",
)
_KIN8_SHARED_HOUSE = _scene("4072", title="KIN8 Shared House", date="2025-07-31", studio="Kin8tengoku")
_OTHER_4072 = _scene("4072", title="Aiden Starr and Danny Wylde", date="2013-02-04", studio="Men In Pain")
_SSIS_497 = _scene("SSIS-497", title="Sample Title", date="2024-01-01", studio="S1")
_BLACKED = _scene("blacked:first-anal", title="First Anal", date="2026-03-29", studio="Blacked")
_BLACKED_SAME_DAY = _scene("blacked:another", title="Another", date="2026-03-29", studio="Blacked")
_BLACKED_OTHER_STUDIO = _scene("blackedraw:first-anal", title="First Anal", date="2026-03-29", studio="BlackedRaw")

PICK_SCENE_CASES: list[tuple[str, list[dict[str, Any]], dict[str, Any] | None]] = [
    # kin8 番号带站前缀, TPDB 的 code 是裸 4 位 movie_id
    ("KIN8-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    ("kin8-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    ("KIN8TENGOKU-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    ("KIN8 0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    # 他厂同号: 有 kin8 条目时取 kin8, 只有他厂条目不回退首条, 序号不同也不命中
    ("KIN8-4072", [_OTHER_4072, _KIN8_SHARED_HOUSE], _KIN8_SHARED_HOUSE),
    ("KIN8-4072", [_OTHER_4072], None),
    ("KIN8-4072", [_KIN8_CUTIE], None),
    # 非 kin8 番号: code 相等仍是唯一判据, 裸数字 code 不被其它前缀借用
    ("SSIS-497", [_SSIS_497], _SSIS_497),
    ("HEYZO-1234", [_KIN8_CUTIE], None),
    # 欧美日期号按片商与发布日期确认, 只认唯一命中
    ("Blacked.26.03.29", [_BLACKED], _BLACKED),
    ("Blacked.26.03.29", [_BLACKED, _BLACKED_SAME_DAY], None),
    ("Blacked.26.03.29", [_BLACKED_OTHER_STUDIO], None),
    # 缺前缀或空番号不命中
    ("KIN8-", [_KIN8_CUTIE], None),
    ("", [_KIN8_CUTIE], None),
]


@pytest.mark.parametrize(("number", "results", "expected"), PICK_SCENE_CASES)
def test_pick_scene(number: str, results: list[dict[str, Any]], expected: dict[str, Any] | None) -> None:
    assert pick_scene(results, number) == expected


@pytest.mark.parametrize("results", [None, "not-a-list", [], [1, "x"], [{"code": None}], [{"studio": {}}]])
def test_pick_scene_rejects_unusable_results(results: object) -> None:
    """应答结构不符时不命中, 也不抛异常."""
    assert pick_scene(results, "KIN8-0236") is None


def _crawler(web: AsyncMock) -> ThePornDBCrawler:
    return ThePornDBCrawler(client=HttpClient(web=web), config=SiteConfig(api_token=_TOKEN))


FETCH_CASES: list[tuple[str, list[dict[str, Any]], tuple[str, str, str, str] | None]] = [
    # 带前缀的 kin8 番号命中, 应答按 code / title / date / studio 投影
    ("KIN8-0236", [_KIN8_CUTIE], ("0236", "Kin8 Cutie", "2010-05-27", "Kin8tengoku")),
    ("KIN8TENGOKU-0236", [_KIN8_CUTIE], ("0236", "Kin8 Cutie", "2010-05-27", "Kin8tengoku")),
    # 他厂同号排在前面的候选被排除
    ("KIN8-4072", [_OTHER_4072, _KIN8_SHARED_HOUSE], ("4072", "KIN8 Shared House", "2025-07-31", "Kin8tengoku")),
    ("KIN8-4072", [_OTHER_4072], None),
    # 非 kin8 番号行为不变
    ("SSIS-497", [_SSIS_497], ("SSIS-497", "Sample Title", "2024-01-01", "S1")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("number", "scenes", "expected"), FETCH_CASES)
async def test_fetch(number: str, scenes: list[dict[str, Any]], expected: tuple[str, str, str, str] | None) -> None:
    """检索词保持番号原样 (裸 movie_id 在 TPDB 上检索不到), 命中后投影为元数据."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": scenes}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number=number))

    assert web.post_json.await_args.kwargs["json"]["variables"] == {"term": number}
    if expected is None:
        assert metadata is None
        return
    assert metadata is not None
    assert (metadata.number, metadata.title, metadata.release, metadata.studio) == expected


@pytest.mark.asyncio
async def test_fetch_western_type_filter() -> None:
    """kin8 归欧美链后经 ``?type=Scene`` 检索."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"searchScene": [_KIN8_CUTIE]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="KIN8-0236", content_type=ContentType.WESTERN))

    assert metadata is not None
    assert metadata.number == "0236"
    assert web.post_json.await_args.args[0] == "https://theporndb.net/graphql?type=Scene"
