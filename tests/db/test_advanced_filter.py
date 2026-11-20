import asyncio
import threading
import time
from typing import TYPE_CHECKING

import aiosqlite
import pytest
from sqlalchemy import literal, not_, select, text

from amane.api.models.filter_expression import MetadataSearchRequest
from amane.api.support.filter_expression import _uncensored_condition, compile_metadata_filter
from amane.db.models import ActorUserTag, FacetKind, UserTag
from amane.db.query_budget import QueryBudgetExceeded, sqlite_query_budget
from amane.db.repo_types import ActorBrowseParams
from amane.parsing import ContentType, Mosaic

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

    from amane.db.repository import Repository


def _compile(expression: dict[str, object]) -> ColumnElement[bool]:
    request = MetadataSearchRequest.model_validate({"version": 1, "expression": expression})
    condition = compile_metadata_filter(request.expression)
    if condition is None:
        raise AssertionError("expected a compiled expression")
    return condition


@pytest.mark.asyncio(loop_scope="function")
async def test_metadata_filter_groups_unknown_ids_and_literal_contains(repo: Repository) -> None:
    first = await repo.upsert_metadata(number="AF-GROUP-1", title="Sale 50%_OFF", actors=["Filter Alpha"])
    second = await repo.upsert_metadata(number="AF-GROUP-2", title="Sale 50XXOFF", actors=["Filter Beta"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_id = next(actor.id for actor in actors if actor.name == "Filter Alpha")
    assert actor_id is not None

    either = _compile(
        {
            "type": "group",
            "op": "or",
            "children": [
                {"type": "rule", "field": "actor_id", "operator": "eq", "value": 999_999},
                {"type": "rule", "field": "actor_id", "operator": "eq", "value": actor_id},
            ],
        }
    )
    items, total = await repo.list_metadata(advanced_filter=either, limit=20)
    assert total == 1
    assert [item.number for item in items] == [first.number]

    negated_unknown = _compile(
        {
            "type": "group",
            "op": "and",
            "negate": True,
            "children": [{"type": "rule", "field": "actor_id", "operator": "eq", "value": 999_999}],
        }
    )
    complement, complement_total = await repo.list_metadata(advanced_filter=negated_unknown, limit=20)
    assert complement_total == 2
    assert {item.id for item in complement} == {first.id, second.id}

    literal = _compile(
        {
            "type": "group",
            "op": "and",
            "children": [{"type": "rule", "field": "title", "operator": "contains", "value": "%_"}],
        }
    )
    literal_items, literal_total = await repo.list_metadata(advanced_filter=literal, limit=20)
    assert literal_total == 1
    assert [item.number for item in literal_items] == [first.number]


@pytest.mark.asyncio(loop_scope="function")
async def test_media_file_rules_can_match_different_files(repo: Repository) -> None:
    await repo.create_library(name="filter-files", path="/filter-files")
    matched = await repo.upsert_metadata(number="AF-FILES-1")
    only_mosaic = await repo.upsert_metadata(number="AF-FILES-2")
    assert matched.id is not None and only_mosaic.id is not None

    cracked = await repo.create_media_file(library_id=1, path="/filter-files/AF-FILES-1-cracked.mp4")
    definition = await repo.create_media_file(library_id=1, path="/filter-files/AF-FILES-1-1080p.mp4")
    other_cracked = await repo.create_media_file(library_id=1, path="/filter-files/AF-FILES-2-cracked.mp4")
    assert cracked.id is not None and definition.id is not None and other_cracked.id is not None
    assert cracked.mosaic == Mosaic.CRACKED
    assert definition.definition == "1080p"
    assert other_cracked.mosaic == Mosaic.CRACKED
    await repo.update_media_file(cracked.id, metadata_id=matched.id)
    await repo.update_media_file(definition.id, metadata_id=matched.id)
    await repo.update_media_file(other_cracked.id, metadata_id=only_mosaic.id)

    expression = _compile(
        {
            "type": "group",
            "op": "and",
            "children": [
                {"type": "rule", "field": "mosaic", "operator": "eq", "value": "cracked"},
                {"type": "rule", "field": "definition", "operator": "eq", "value": "1080p"},
            ],
        }
    )
    items, total = await repo.list_metadata(advanced_filter=expression, limit=20)
    assert total == 1
    assert [item.number for item in items] == [matched.number]


@pytest.mark.asyncio(loop_scope="function")
async def test_uncensored_false_is_complement_and_null_predicate_is_two_valued(repo: Repository) -> None:
    censored = await repo.upsert_metadata(number="AF-CENSORED", content_type=ContentType.CENSORED)
    uncensored = await repo.upsert_metadata(number="AF-UNCENSORED", content_type=ContentType.UNCENSORED)

    false_filter = _compile(
        {
            "type": "group",
            "op": "and",
            "children": [{"type": "rule", "field": "uncensored", "operator": "eq", "value": False}],
        }
    )
    false_items, _ = await repo.list_metadata(advanced_filter=false_filter, limit=20)
    not_true_filter = _compile(
        {
            "type": "group",
            "op": "and",
            "negate": True,
            "children": [{"type": "rule", "field": "uncensored", "operator": "eq", "value": True}],
        }
    )
    not_true_items, _ = await repo.list_metadata(advanced_filter=not_true_filter, limit=20)
    assert {item.id for item in false_items} == {censored.id}
    assert {item.id for item in not_true_items} == {censored.id}
    assert uncensored.id not in {item.id for item in false_items}

    predicate = _uncensored_condition(literal(None) == ContentType.UNCENSORED, literal(False))
    async with repo._session() as session:
        value, complement = (await session.execute(select(predicate, not_(predicate)))).one()
    assert value is False
    assert complement is True


@pytest.mark.asyncio(loop_scope="function")
async def test_sqlite_query_budget_restores_handler_after_success_interrupt_and_cancel(repo: Repository) -> None:
    long_query = text(
        "WITH RECURSIVE counter(value) AS (SELECT 1 UNION ALL SELECT value + 1 FROM counter WHERE value < 50000) "
        "SELECT max(value) FROM counter"
    )
    async with repo._session() as session:
        connection = await session.connection()
        initial_driver = (await connection.get_raw_connection()).driver_connection

        async with sqlite_query_budget(session, time.monotonic() + 1.0):
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        completed = (await session.execute(long_query)).scalar_one()
        assert completed == 50000

        with pytest.raises(QueryBudgetExceeded):
            async with sqlite_query_budget(session, time.monotonic() + 0.001):
                await session.execute(long_query)
        await session.rollback()
        current_connection = await session.connection()
        current_driver = (await current_connection.get_raw_connection()).driver_connection
        assert current_driver is initial_driver
        assert (await session.execute(long_query)).scalar_one() == 50000

        assert isinstance(current_driver, aiosqlite.Connection)
        started = threading.Event()
        release = threading.Event()

        def mark_started() -> int:
            started.set()
            release.wait(timeout=10.0)
            return 1

        await current_driver.create_function("mark_filter_started", 0, mark_started)

        async def cancelled_query() -> None:
            async with sqlite_query_budget(session, time.monotonic() + 1.0):
                await session.execute(text("SELECT mark_filter_started()"))

        task = asyncio.create_task(cancelled_query())
        assert await asyncio.to_thread(started.wait, 10.0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await session.rollback()
        final_connection = await session.connection()
        final_driver = (await final_connection.get_raw_connection()).driver_connection
        assert final_driver is not initial_driver
        assert (await session.execute(long_query)).scalar_one() == 50000

        now = 10.0

        def clock() -> float:
            return now

        with pytest.raises(QueryBudgetExceeded):
            async with sqlite_query_budget(session, 11.0, clock=clock):
                assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
                now = 12.0
                await session.execute(long_query)


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_user_tag_filter_aggregates_more_than_one_thousand_ids(repo: Repository) -> None:
    await repo.upsert_metadata(number="AF-TAGS", actors=["All Tags", "Partial Tags"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_ids = {actor.name: actor.id for actor in actors if actor.name in {"All Tags", "Partial Tags"}}
    all_actor_id = actor_ids["All Tags"]
    partial_actor_id = actor_ids["Partial Tags"]
    assert all_actor_id is not None and partial_actor_id is not None

    tags = [UserTag(name=f"large-filter-{index}") for index in range(1001)]
    async with repo._session() as session:
        session.add_all(tags)
        await session.flush()
        tag_ids = [tag.id for tag in tags if tag.id is not None]
        assert len(tag_ids) == 1001
        session.add_all([ActorUserTag(actor_id=all_actor_id, user_tag_id=tag_id) for tag_id in tag_ids])
        session.add_all([ActorUserTag(actor_id=partial_actor_id, user_tag_id=tag_id) for tag_id in tag_ids[:-1]])
        await session.commit()

    all_tags, _ = await repo.browse_actors(ActorBrowseParams(user_tag_ids=tag_ids, limit=20))
    assert {actor.name for actor in all_tags} == {"All Tags"}

    duplicate_ids, _ = await repo.browse_actors(
        ActorBrowseParams(user_tag_ids=[*tag_ids, tag_ids[0], tag_ids[0]], limit=20)
    )
    assert {actor.name for actor in duplicate_ids} == {"All Tags"}

    partial_match, _ = await repo.browse_actors(ActorBrowseParams(user_tag_ids=tag_ids[:-1], limit=20))
    assert {actor.name for actor in partial_match} == {"All Tags", "Partial Tags"}

    unknown, _ = await repo.browse_actors(ActorBrowseParams(user_tag_ids=[*tag_ids, 999_999], limit=20))
    assert unknown == []
