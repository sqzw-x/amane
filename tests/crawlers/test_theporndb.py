"""theporndb 爬虫单元测试: 检索与指纹的命中判据、应答投影.

应答按 TPDB 上的真实字段值构造: ``code`` 是 kin8 站内 4 位 ``movie_id``, 他厂会用同一个数字当番号.
命中判据只留分支代表, 番号前缀与补零宽度的其余形态见 ``tests/crawlers/test_kin8.py``.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.theporndb import ThePornDBCrawler, pick_fingerprint_scene, pick_scene
from amane.parsing import ContentType

_TOKEN = "test-token"
_OSHASH = "859a35d864c0184b"


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
_KIN8_0001 = _scene("0001", title="KIN8 First", date="2009-01-14", studio="Kin8tengoku")
_OTHER_4072 = _scene("4072", title="Aiden Starr and Danny Wylde", date="2013-02-04", studio="Men In Pain")
_SSIS_497 = _scene("SSIS-497", title="Sample Title", date="2024-01-01", studio="S1")
_BLACKED = _scene("blacked:first-anal", title="First Anal", date="2026-03-29", studio="Blacked")
_BLACKED_SAME_DAY = _scene("blacked:another", title="Another", date="2026-03-29", studio="Blacked")
_BLACKED_OTHER_STUDIO = _scene("blackedraw:first-anal", title="First Anal", date="2026-03-29", studio="BlackedRaw")
_TUSHY = _scene(
    "tushy:i-only-gape-for-you",
    title="I Only Gape for You",
    date="2018-02-05",
    studio="Tushy",
    scene_id="69e638f5-c687-4ae3-ae68-a46f691f38a2",
)
_SEXART = _scene(
    "20120413_sexart",
    title="SexArt",
    date="2012-04-13",
    studio="SexArt",
    scene_id="0b9d67e2-bc43-4041-bb13-3ef81a33a89f",
)

PICK_SCENE_CASES: list[tuple[str, list[dict[str, Any]], dict[str, Any] | None]] = [
    # kin8 番号带站前缀, TPDB 的 code 是站内 4 位补零 id
    ("KIN8-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    ("KIN8TENGOKU-0236", [_KIN8_CUTIE], _KIN8_CUTIE),
    # 大小写归并与去零后补零到 4 位同由该行覆盖; 越界拒绝见下一行, 其余宽度形态见 test_kin8.py
    ("kin8-001", [_KIN8_0001], _KIN8_0001),
    ("KIN8-10000", [_KIN8_0001], None),
    # 他厂同号: 候选里有 kin8 条目时取 kin8 条目, 只有他厂条目不回退首条
    ("KIN8-4072", [_OTHER_4072, _KIN8_SHARED_HOUSE], _KIN8_SHARED_HOUSE),
    ("KIN8-4072", [_OTHER_4072], None),
    # 非 kin8 番号: code 相等仍是唯一判据
    ("SSIS-497", [_SSIS_497], _SSIS_497),
    # 欧美日期号按片商与发布日期确认: 同日的他厂候选不命中, 只认唯一命中
    ("Blacked.26.03.29", [_BLACKED_OTHER_STUDIO, _BLACKED], _BLACKED),
    ("Blacked.26.03.29", [_BLACKED, _BLACKED_SAME_DAY], None),
]


@pytest.mark.parametrize(("number", "results", "expected"), PICK_SCENE_CASES)
def test_pick_scene(number: str, results: list[dict[str, Any]], expected: dict[str, Any] | None) -> None:
    assert pick_scene(results, number) == expected


@pytest.mark.parametrize("results", [None, [], [None, {"code": None}]])
def test_pick_scene_rejects_unusable_results(results: object) -> None:
    """应答结构不符 (不是列表、空表、元素不是字典或缺 ``code``) 时不命中, 也不抛异常."""
    assert pick_scene(results, "KIN8-0236") is None


def _crawler(web: AsyncMock) -> ThePornDBCrawler:
    return ThePornDBCrawler(client=HttpClient(web=web), config=SiteConfig(api_token=_TOKEN))


FETCH_CASES: list[tuple[str, list[dict[str, Any]], tuple[str, str, str, str] | None]] = [
    # 命中后按 code / title / date / studio 投影
    ("KIN8-0236", [_KIN8_CUTIE], ("0236", "Kin8 Cutie", "2010-05-27", "Kin8tengoku")),
    # 只有他厂同号候选: 链路走通但不命中
    ("KIN8-4072", [_OTHER_4072], None),
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
@pytest.mark.parametrize(
    "url",
    [
        "https://theporndb.net/scenes/96842b9f-0405-481b-a44a-1c7fb5b0370c",
        "https://theporndb.net/scenes/96842b9f-0405-481b-a44a-1c7fb5b0370c/some-title",
        "gql://scene/96842b9f-0405-481b-a44a-1c7fb5b0370c",
    ],
)
async def test_fetch_by_url_queries_scene_id(url: str) -> None:
    """公开作品页与内部检索地址都按 id 取单条, 不经过检索."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"findScene": _KIN8_CUTIE}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="", url=url))

    assert metadata is not None
    assert metadata.number == "0236"
    assert web.post_json.await_args.kwargs["json"]["variables"] == {"id": "96842b9f-0405-481b-a44a-1c7fb5b0370c"}


@pytest.mark.asyncio
async def test_fetch_by_url_rejects_non_scene_path() -> None:
    web = AsyncMock()
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="", url="https://theporndb.net/movies/123"))

    assert metadata is None
    web.post_json.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scene", "slug", "expected"),
    [
        (_TUSHY, "tushy-i-only-gape-for-you", "Tushy.18.02.05"),
        (_SEXART, "sexart-sexart", "SexArt.12.04.13"),
    ],
)
async def test_fetch_by_url_uses_western_date_number(scene: dict[str, Any], slug: str, expected: str) -> None:
    """欧美条目的 code 不是番号; 条目按文件侧的 Studio.YY.MM.DD 建立."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"findScene": scene}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="", url=f"https://theporndb.net/scenes/{slug}"))

    assert metadata is not None
    assert metadata.number == expected


@pytest.mark.asyncio
async def test_fetch_by_url_keeps_jav_number() -> None:
    """JAV 的 code 本身就是番号, 不按欧美日期号改写."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"findScene": _SSIS_497}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="", url="https://theporndb.net/scenes/ssis-497"))

    assert metadata is not None
    assert metadata.number == "SSIS-497"


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


FINGERPRINT_SHAPES: list[tuple[object, dict[str, Any] | None]] = [
    ([[_BLACKED]], _BLACKED),
    # 同一个指纹会挂多条 OSHASH 变体, 应答里的首条即命中
    ([[_BLACKED, _BLACKED_SAME_DAY]], _BLACKED),
    ([[]], None),
    # 外层按入参逐条对应, 只发一个指纹, 第二组不参与判定
    ([[], [_BLACKED]], None),
    # 上游两层非空, TPDB 的兼容实现都可空: 缺值不能抛, 也不能当成命中
    ([None], None),
    ([[None]], None),
    (None, None),
    ({}, None),
]


@pytest.mark.parametrize(("results", "expected"), FINGERPRINT_SHAPES)
def test_pick_fingerprint_scene(results: object, expected: dict[str, Any] | None) -> None:
    """批量指纹应答逐层按可空展开, 只看第一组."""
    assert pick_fingerprint_scene(results) == expected


@pytest.mark.asyncio
async def test_fetch_by_fingerprint() -> None:
    """指纹命中走 stash-box 批量字段, 命中后不再发番号检索."""
    web = AsyncMock()
    web.post_json.return_value = {"data": {"findScenesBySceneFingerprints": [[_BLACKED]]}}
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="Blacked.26.03.29", file_hash=_OSHASH))

    assert metadata is not None
    assert (metadata.number, metadata.studio) == ("blacked:first-anal", "Blacked")
    assert web.post_json.await_count == 1
    body = web.post_json.await_args.kwargs["json"]
    # 变量按上游最严格的可空标注声明, TPDB 的宽松声明同样接受
    assert "$fingerprints: [[FingerprintQueryInput!]!]!" in body["query"]
    assert body["variables"] == {"fingerprints": [[{"hash": _OSHASH, "algorithm": "OSHASH"}]]}


FINGERPRINT_MISS_CASES: list[dict[str, Any]] = [
    {"data": {"findScenesBySceneFingerprints": [[]]}},
    {"data": {"findScenesBySceneFingerprints": [None]}},
    {"data": None},
    # HTTP 200 但 GraphQL 层报错: 不能中断整次刮削
    {"errors": [{"message": 'Cannot query field "findScenesBySceneFingerprints"'}]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fingerprint_payload", FINGERPRINT_MISS_CASES)
async def test_fingerprint_miss_falls_back_to_search(fingerprint_payload: dict[str, Any]) -> None:
    """指纹没命中或应答不可用时按番号检索, 结果照常投影."""
    web = AsyncMock()
    web.post_json.side_effect = [fingerprint_payload, {"data": {"searchScene": [_KIN8_CUTIE]}}]
    crawler = _crawler(web)

    metadata = await crawler.fetch(SearchQuery(number="KIN8-0236", file_hash=_OSHASH))

    assert metadata is not None
    assert metadata.number == "0236"
    assert web.post_json.await_count == 2
