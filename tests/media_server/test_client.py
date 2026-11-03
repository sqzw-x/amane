"""Emby / Jellyfin 客户端: 认证, 分页, 完整 DTO 写回与 Base64 头像."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx2
import pytest

from amane.config import MediaServerConfig
from amane.media_server import (
    MediaPerson,
    MediaServerClient,
    MediaServerError,
    find_persons,
    index_persons,
    match_persons,
    normalize_person_name,
)

_CONFIG = MediaServerConfig(kind="jellyfin", url="http://media.local:8096/", api_key="secret")


def _person_dto(
    *,
    person_id: str = "p1",
    name: str = "Alice",
    overview: str | None = "old",
    image: bool = False,
    lock_data: bool = False,
) -> dict[str, object]:
    return {
        "Id": person_id,
        "Name": name,
        "ForcedSortName": "Alice Sort",
        "OriginalTitle": None,
        "OriginalLanguage": "ja",
        "CriticRating": None,
        "CommunityRating": None,
        "IndexNumber": None,
        "ParentIndexNumber": None,
        "Overview": overview,
        "EndDate": None,
        "PremiereDate": "1990-01-01T00:00:00",
        "ProductionYear": 1990,
        "OfficialRating": None,
        "CustomRating": None,
        "PreferredMetadataLanguage": "ja",
        "PreferredMetadataCountryCode": "JP",
        "LockData": lock_data,
        "LockedFields": [],
        "Genres": ["Drama"],
        "Tags": ["tag"],
        "People": [{"Name": "Bob", "Type": "Actor"}],
        "ProductionLocations": ["Japan"],
        "ProviderIds": {"Imdb": "tt1"},
        "ServerId": "srv",
        "Type": "Person",
        "UserData": {"Played": True},
        "ImageTags": {"Primary": "tag"} if image else {},
    }


def _transport(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.asyncio(loop_scope="function")
async def test_list_persons_paginates_and_keeps_auth_out_of_query() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        start = int(request.url.params["startIndex"])
        if start == 0:
            return httpx2.Response(
                200,
                json={
                    "Items": [_person_dto(person_id="p1"), _person_dto(person_id="p2", name="Bob")],
                    "TotalRecordCount": 3,
                },
            )
        return httpx2.Response(200, json={"Items": [_person_dto(person_id="p3", name="Carol")], "TotalRecordCount": 3})

    async with MediaServerClient(_CONFIG, client=_transport(handler)) as client:
        persons = await client.list_persons()

    assert [person.name for person in persons] == ["Alice", "Bob", "Carol"]
    assert [request.url.params["startIndex"] for request in requests] == ["0", "2"]
    assert all("fields" in request.url.params for request in requests)
    assert "PremiereDate" in requests[0].url.params["fields"]
    assert "Genres" in requests[0].url.params["fields"]
    assert "Tags" in requests[0].url.params["fields"]
    assert "People" in requests[0].url.params["fields"]
    assert "ProviderIds" in requests[0].url.params["fields"]
    assert all(request.url.params["enableImages"] == "true" for request in requests)
    assert all("secret" not in str(request.url) for request in requests)
    assert requests[0].headers["X-Emby-Token"] == "secret"
    assert requests[0].headers["Authorization"] == 'MediaBrowser Token="secret"'


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        (({"Items": [_person_dto()]},), "TotalRecordCount"),
        (({"Items": [_person_dto()], "TotalRecordCount": 0},), "TotalRecordCount"),
        (({"Items": [_person_dto()], "TotalRecordCount": -1},), "TotalRecordCount"),
        (({"Items": [_person_dto(), _person_dto(person_id="p2")], "TotalRecordCount": 1},), "超过"),
        (
            (
                {"Items": [_person_dto()], "TotalRecordCount": 2},
                {"Items": [], "TotalRecordCount": 2},
            ),
            "空页",
        ),
        (({"Items": [_person_dto()], "TotalRecordCount": 2},) * 2, "重复"),
    ],
    ids=["no-total", "zero-total", "negative-total", "over-total", "empty-page", "repeated-page"],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_list_persons_rejects_incomplete_pages(pages: tuple[dict, ...], message: str) -> None:
    """重复页、提前出现的空页和无效总数都不能当成读完."""
    served = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal served
        payload = pages[min(served, len(pages) - 1)]
        served += 1
        return httpx2.Response(200, json=payload)

    async with MediaServerClient(_CONFIG, client=_transport(handler)) as client:
        with pytest.raises(MediaServerError, match=message):
            await client.list_persons()

    assert served == len(pages)


@pytest.mark.asyncio(loop_scope="function")
async def test_list_persons_returns_empty_for_empty_server() -> None:
    async with MediaServerClient(
        _CONFIG,
        client=_transport(lambda _: httpx2.Response(200, json={"Items": [], "TotalRecordCount": 0})),
    ) as client:
        assert await client.list_persons() == []


@pytest.mark.asyncio(loop_scope="function")
async def test_update_person_sends_current_dto_and_only_changes_requested_fields() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.path == "/Items/p1"
        return httpx2.Response(204)

    person = MediaPerson.from_dto(_person_dto(lock_data=True))
    assert person is not None

    async with MediaServerClient(_CONFIG, client=_transport(handler)) as client:
        await client.update_person(person, {"Overview": "new", "ProductionLocations": ["Japan"]})

    assert len(requests) == 1
    body = json.loads(requests[0].content)
    # 写回以读取到的当前值为底, 只替换请求的字段.
    assert body["Overview"] == "new"
    assert body["ProductionLocations"] == ["Japan"]
    carried = {
        "Name": "Alice",
        "ForcedSortName": "Alice Sort",
        "OriginalLanguage": "ja",
        "PremiereDate": "1990-01-01T00:00:00",
        "ProductionYear": 1990,
        "PreferredMetadataLanguage": "ja",
        "PreferredMetadataCountryCode": "JP",
        "LockData": True,
        "LockedFields": [],
        "Genres": ["Drama"],
        "Tags": ["tag"],
        "ProviderIds": {"Imdb": "tt1"},
    }
    assert {key: body[key] for key in carried} == carried
    # 读取模型没有写回语义, 不能随请求发出.
    assert {"ImageTags", "People", "UserData", "ServerId", "Type"}.isdisjoint(body)


@pytest.mark.parametrize("lock_state", [None, "false", 0])
@pytest.mark.asyncio(loop_scope="function")
async def test_update_person_rejects_missing_lock_state(lock_state: object) -> None:
    person = MediaPerson.from_dto(_person_dto())
    assert person is not None
    person.raw["LockData"] = lock_state

    async with MediaServerClient(_CONFIG, client=_transport(lambda _: httpx2.Response(204))) as client:
        with pytest.raises(MediaServerError, match="LockData"):
            await client.update_person(person, {"Overview": "new"})


@pytest.mark.asyncio(loop_scope="function")
async def test_upload_primary_image_is_base64() -> None:
    requests: list[httpx2.Request] = []
    raw = b"\x89PNG\r\n"

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(204)

    async with MediaServerClient(_CONFIG, client=_transport(handler)) as client:
        await client.upload_primary_image("p1", raw, content_type="image/png")

    assert requests[0].url.path == "/Items/p1/Images/Primary"
    assert requests[0].headers["Content-Type"] == "image/png"
    assert base64.b64decode(requests[0].content) == raw


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "认证失败"), (403, "权限不足")],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_auth_errors_are_distinct(status: int, message: str) -> None:
    async with MediaServerClient(
        _CONFIG, client=_transport(lambda _: httpx2.Response(status, text="denied"))
    ) as client:
        with pytest.raises(MediaServerError, match=message):
            await client.list_persons()


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("希崎 ジェシカ", "希崎ジェシカ", True),
        ("Ａｌｉｃｅ", "alice", True),
        ("Alice", "Alicia", False),
        ("", "", True),
    ],
)
def test_name_normalization(left: str, right: str, expected: bool) -> None:
    assert (normalize_person_name(left) == normalize_person_name(right)) is expected


def test_matching_uses_display_name_and_aliases() -> None:
    alice = MediaPerson(id="p1", name="Alice")
    alice_alias = MediaPerson(id="p2", name="ありす")
    bob = MediaPerson(id="p3", name="Bob")
    index = index_persons([alice, alice_alias, bob])

    found = find_persons(index, ["Alice", "ありす", "Alice"])
    assert [person.id for person in found] == ["p1", "p2"]

    assert [person.id for person in match_persons(["ありす"], [alice, alice_alias, bob])] == ["p2"]
    assert match_persons([], [alice]) == []
