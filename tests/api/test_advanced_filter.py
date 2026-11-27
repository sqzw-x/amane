import asyncio
from typing import TYPE_CHECKING

import pytest
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

import amane.db.query_budget as query_budget
from amane.api.middleware import FilterRequestBodyLimitMiddleware
from amane.api.routes import metadata as metadata_routes
from amane.db.models import FacetKind, Metadata
from amane.enums import ActorGender

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_search_sql_interrupt_returns_503_and_following_requests_succeed(
    client: AsyncClient, repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    await repo.upsert_metadata(number="AF-BUDGET-API")
    rows = [Metadata(number=f"AF-BUDGET-BULK-{index:04d}") for index in range(5000)]
    async with repo._session() as session:
        session.add_all(rows)
        await session.commit()

    body = {
        "version": 1,
        "expression": {
            "type": "group",
            "op": "or",
            "children": [
                {"type": "rule", "field": "actor_id", "operator": "eq", "value": 900_000 + index}
                for index in range(200)
            ],
        },
    }
    with monkeypatch.context() as patcher:
        patcher.setattr(metadata_routes, "DEFAULT_ADVANCED_FILTER_TIMEOUT_SECONDS", 0.01)
        patcher.setattr(query_budget, "_PROGRESS_STEPS", 100)
        timed_out = await client.post("metadata/search", json=body)
        assert timed_out.status_code == 503, timed_out.text
        assert "超时" in timed_out.json()["detail"]

    for method in ("GET", "POST"):
        if method == "GET":
            recovered = await client.get("metadata", params={"search": "AF-BUDGET-API"})
        else:
            recovered = await client.post("metadata/search", json={"version": 1, "search": "AF-BUDGET-API"})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["total"] == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_advanced_search_concurrency_overload_recovers_after_slot_release() -> None:
    calls = 0
    messages: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = FilterRequestBodyLimitMiddleware(app, max_concurrent=1, queue_timeout_seconds=0.001)
    scope: Scope = {"type": "http", "method": "POST", "path": "/api/metadata/search", "headers": []}

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"version":1}', "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware._search_slots.acquire()
    await middleware(scope, receive, send)
    assert calls == 0
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 503
    middleware._search_slots.release()
    messages.clear()
    await middleware(scope, receive, send)
    assert calls == 1
    assert messages[0]["status"] == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_slow_bodies_do_not_occupy_search_slots() -> None:
    calls = 0
    started = 0
    both_reading = asyncio.Event()
    messages: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = FilterRequestBodyLimitMiddleware(app, max_concurrent=2, queue_timeout_seconds=0.01)
    slow_scope: Scope = {"type": "http", "method": "POST", "path": "/api/metadata/search", "headers": []}

    async def stalled_receive() -> Message:
        nonlocal started
        started += 1
        if started == 2:
            both_reading.set()
        await asyncio.Event().wait()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def normal_receive() -> Message:
        return {"type": "http.request", "body": b'{"version":1}', "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    slow = [asyncio.create_task(middleware(slow_scope, stalled_receive, send)) for _ in range(2)]
    await both_reading.wait()
    fast_scope: Scope = {"type": "http", "method": "POST", "path": "/api/actors/search", "headers": []}
    await middleware(fast_scope, normal_receive, send)
    assert calls == 1
    assert messages[0]["status"] == 200
    for task in slow:
        task.cancel()
    await asyncio.gather(*slow, return_exceptions=True)


@pytest.mark.asyncio(loop_scope="function")
async def test_slow_body_times_out_without_consuming_a_search_slot() -> None:
    calls = 0
    messages: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        calls += 1
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = FilterRequestBodyLimitMiddleware(app, body_read_timeout_seconds=1.0, body_idle_timeout_seconds=0.01)
    scope: Scope = {"type": "http", "method": "POST", "path": "/api/metadata/search", "headers": []}

    async def stalled_receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await middleware(scope, stalled_receive, send)
    assert calls == 0
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 408


def _rule(field: str, operator: str, value: object) -> dict[str, object]:
    return {"type": "rule", "field": field, "operator": operator, "value": value}


def _group(*children: dict[str, object], op: str = "and", negate: bool | None = None) -> dict[str, object]:
    node: dict[str, object] = {"type": "group", "op": op, "children": list(children)}
    if negate is not None:
        node["negate"] = negate
    return node


@pytest.mark.asyncio(loop_scope="function")
async def test_metadata_post_filters_and_search_is_outer_and(client: AsyncClient, repo: Repository) -> None:
    first = await repo.upsert_metadata(number="AF-API-1", title="First title", actors=["Filter Actor"])
    second = await repo.upsert_metadata(number="AF-API-2", title="Second title", actors=["Other Actor"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_id = next(actor.id for actor in actors if actor.name == "Filter Actor")
    assert actor_id is not None

    response = await client.post(
        "metadata/search",
        json={"version": 1, "expression": _group(_rule("actor_id", "eq", actor_id))},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [item["number"] for item in response.json()["items"]] == [first.number]

    narrowed_by_search = await client.post(
        "metadata/search",
        json={"version": 1, "search": "AF-API-2", "expression": _group(_rule("actor_id", "eq", actor_id))},
    )
    assert narrowed_by_search.status_code == 200
    assert narrowed_by_search.json()["items"] == []
    assert narrowed_by_search.json()["total"] == 0

    unfiltered = await client.post("metadata/search", json={"version": 1, "expression": None})
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total"] == 2
    assert {item["id"] for item in unfiltered.json()["items"]} == {first.id, second.id}


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_post_preserves_gender_neutral_default_and_accepts_alias_search(
    client: AsyncClient, repo: Repository
) -> None:
    await repo.upsert_metadata(number="AF-ACTOR-API", actors=["Search Female", "Search Male"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_by_name = {actor.name: actor.id for actor in actors if actor.name.startswith("Search ")}
    female_id = actor_by_name["Search Female"]
    male_id = actor_by_name["Search Male"]
    assert female_id is not None and male_id is not None

    female = await repo.get_actor(female_id)
    male = await repo.get_actor(male_id)
    assert female is not None and male is not None
    female.gender = ActorGender.FEMALE
    male.gender = ActorGender.MALE
    await repo.save_actor(female, aliases=["Alias Needle"])
    await repo.save_actor(male)

    neutral = await client.post("actors/search", json={"version": 1})
    assert neutral.status_code == 200
    assert neutral.json()["total"] == 2
    assert {item["gender"] for item in neutral.json()["items"]} == {"female", "male"}

    gender = await client.post(
        "actors/search",
        json={"version": 1, "expression": _group(_rule("gender", "eq", "male"))},
    )
    assert gender.status_code == 200
    assert [item["id"] for item in gender.json()["items"]] == [male_id]

    alias_search = await client.post("actors/search", json={"version": 1, "search": "Alias Needle"})
    assert alias_search.status_code == 200
    assert [item["id"] for item in alias_search.json()["items"]] == [female_id]


@pytest.mark.asyncio(loop_scope="function")
async def test_search_rejects_invalid_ast_and_saved_query_mixing(client: AsyncClient) -> None:
    valid = _rule("number", "contains", "ABC")
    too_deep: dict[str, object] = valid
    for _ in range(9):
        too_deep = _group(too_deep)
    too_many_nodes = _group(*[_rule("number", "contains", str(index)) for index in range(256)])
    too_many_ids = _group(*[_rule("actor_id", "eq", index + 1) for index in range(201)])
    invalid_requests: tuple[tuple[str, dict[str, object]], ...] = (
        ("metadata/search", {"version": 1, "expression": _group()}),
        ("metadata/search", {"version": 1, "expression": valid}),
        ("metadata/search", {"version": 1, "expression": _group(_rule("title", "eq", "ABC"))}),
        ("metadata/search", {"version": 1, "expression": _group(_rule("definition", "eq", "bogus"))}),
        ("metadata/search", {"version": 1, "expression": _group(_rule("actor_id", "eq", 1 << 63))}),
        ("metadata/search", {"version": 1, "expression": _group(_rule("number", "contains", "x" * 201))}),
        ("metadata/search", {"version": 1, "expression": too_deep}),
        ("metadata/search", {"version": 1, "expression": too_many_nodes}),
        ("actors/search", {"version": 1, "expression": _group(_rule("actor_id", "eq", 1))}),
        ("actors/search", {"version": 1, "expression": _group(_rule("user_tag_id", "eq", True))}),
        ("actors/search", {"version": 1, "expression": _group(_rule("height", "gte", 1 << 63))}),
        ("actors/search", {"version": 1, "expression": too_many_ids}),
        ("actors/search", {"version": 1, "expression": _group(_rule("cup", "gte", "A1"))}),
        ("actors/search", {"version": 1, "expression": _group(_rule("birthday", "lte", "2024-13-01"))}),
        ("metadata/search", {"version": 1, "saved_query_id": 1}),
        ("actors/search", {"version": 1, "saved_query_id": 1}),
    )
    for path, body in invalid_requests:
        response = await client.post(path, json=body)
        assert response.status_code == 422, (path, body, response.text)

    for path in ("metadata/search", "actors/search"):
        query_mix = await client.post(path, params={"saved_query_id": 1}, json={"version": 1})
        assert query_mix.status_code == 422, (path, query_mix.text)
        oversized = await client.post(path, content=b'{"version":1}' + b" " * 32_768)
        assert oversized.status_code == 413, (path, oversized.text)
