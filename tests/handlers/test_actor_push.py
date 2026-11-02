"""ACTOR_PUSH handler: 补缺/覆盖, 匹配, 头像与失败语义."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.config import ActorScrapingConfig, HotSettings, MediaServerConfig
from amane.db.repo_types import WriteMode
from amane.handlers import ActorPushHandler, ActorPushPayload
from amane.media import ResourceStore
from amane.media_server import MediaPerson, MediaServerError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from amane.db.repository import Repository

_URL = "https://images.example/actor.jpg"


class _FakeClient:
    def __init__(
        self,
        persons: Sequence[MediaPerson],
        *,
        fail_updates: frozenset[str] = frozenset(),
        fail_images: frozenset[str] = frozenset(),
        fail_list: bool = False,
    ) -> None:
        self.persons = list(persons)
        self.fail_updates = fail_updates
        self.fail_images = fail_images
        self.fail_list = fail_list
        self.list_calls = 0
        self.search_calls: list[str] = []
        self.updates: list[tuple[str, dict[str, object]]] = []
        self.images: list[tuple[str, str]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def list_persons(self) -> list[MediaPerson]:
        self.list_calls += 1
        if self.fail_list:
            raise MediaServerError("服务器不可达")
        return self.persons

    async def search_persons(self, term: str) -> list[MediaPerson]:
        self.search_calls.append(term)
        return [person for person in self.persons if term in (person.name, "ありす")]

    async def update_person(self, person: MediaPerson, updates: Mapping[str, object]) -> None:
        if person.name in self.fail_updates:
            raise MediaServerError("写入失败")
        self.updates.append((person.id, dict(updates)))

    async def upload_primary_image(self, person_id: str, data: bytes, *, content_type: str) -> None:
        if person_id in self.fail_images:
            raise MediaServerError("上传失败")
        self.images.append((person_id, content_type))


def _hot(*, auto_push: bool = False) -> HotSettings:
    return HotSettings(
        media_server=MediaServerConfig(url="http://media.local:8096", api_key="key"),
        actor_scraping=ActorScrapingConfig(auto_push=auto_push),
    )


async def _actor(
    repo: Repository,
    name: str,
    *,
    overview: str | None = None,
    birthday: str | None = None,
    birthplace: str | None = None,
    image_urls: list[str] | None = None,
    aliases: list[str] | None = None,
) -> int:
    await repo.upsert_metadata(number=f"AP-{name}", actors=[name])
    actors = await repo.get_actors_by_names([name])
    actor = actors[0]
    assert actor.id is not None
    await repo.update_actor(
        actor.id,
        mode=WriteMode.MANUAL,
        overview=overview,
        birthday=birthday,
        birthplace=birthplace,
        image_urls=image_urls or [],
        aliases=aliases or [],
    )
    return actor.id


async def _image_url(store: ResourceStore, tmp_path: Path) -> str:
    async def producer(dest: Path) -> bool:
        dest.write_bytes(b"\x89PNG\r\n")
        return True

    resource = await store.acquire_derived(_URL, "crop", "actor-push-test", producer, ext=".png")
    assert resource is not None
    return resource.url


def _person(
    name: str,
    *,
    overview: str | None = None,
    premiere_date: str | None = None,
    production_year: int | None = None,
    locations: list[str] | None = None,
    image: bool = False,
    person_id: str | None = None,
) -> MediaPerson:
    return MediaPerson(
        id=person_id or f"p-{name}",
        name=name,
        has_primary_image=image,
        overview=overview,
        premiere_date=premiere_date,
        production_year=production_year,
        production_locations=locations or [],
        raw={"LockData": False},
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_fills_missing_and_skips_existing(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    image_url = await _image_url(resource_store, tmp_path)
    alice = await _actor(
        repo,
        "Alice",
        overview="new bio",
        birthday="1990-01-01",
        birthplace="Tokyo",
        image_urls=[image_url],
    )
    bob = await _actor(repo, "Bob", overview="bob bio")
    client = _FakeClient(
        [
            _person("Alice"),
            _person("Bob", overview="old", image=True),
        ]
    )

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.requested == 2
    assert result.result.matched == 2
    assert result.result.updated == 1
    assert result.result.images == 1
    assert result.result.skipped == 1
    assert result.result.not_found == 0
    assert result.result.no_image == 1
    assert [call[0] for call in client.updates] == ["p-Alice"]
    assert client.updates[0][1] == {
        "Overview": "new bio",
        "PremiereDate": "1990-01-01",
        "ProductionYear": 1990,
        "ProductionLocations": ["Tokyo"],
    }
    assert client.images == [("p-Alice", "image/png")]
    assert client.list_calls == 1
    assert client.search_calls == []
    assert alice and bob


@pytest.mark.asyncio(loop_scope="function")
async def test_force_overwrites_non_empty_values(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    image_url = await _image_url(resource_store, tmp_path)
    await _actor(
        repo,
        "Alice",
        overview="new bio",
        birthday="1990-01-01",
        birthplace="Tokyo",
        image_urls=[image_url],
    )
    client = _FakeClient(
        [
            _person(
                "Alice",
                overview="old",
                premiere_date="1980-01-01T00:00:00",
                production_year=1980,
                locations=["Japan"],
                image=True,
            )
        ]
    )

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload(force=True))

    assert result.success
    assert result.result is not None
    assert result.result.updated == 1
    assert result.result.images == 1
    updates = client.updates[0][1]
    assert updates["Overview"] == "new bio"
    assert updates["PremiereDate"] == "1990-01-01"
    assert updates["ProductionYear"] == 1990
    assert updates["ProductionLocations"] == ["Tokyo"]


@pytest.mark.asyncio(loop_scope="function")
async def test_alias_match_and_not_found(repo: Repository, resource_store: ResourceStore) -> None:
    await _actor(repo, "Alice", overview="bio", aliases=["ありす"])
    await _actor(repo, "Ghost", overview="bio")
    client = _FakeClient([_person("ありす")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload(actor_ids=await _ids(repo, "Alice", "Ghost")))

    assert result.success
    assert result.result is not None
    assert result.result.matched == 1
    assert result.result.updated == 1
    assert result.result.not_found == 1
    assert result.result.no_image == 1
    assert client.search_calls  # 小列表走检索


async def _ids(repo: Repository, *names: str) -> list[int]:
    out: list[int] = []
    for name in names:
        actors = await repo.get_actors_by_names([name])
        actor_id = actors[0].id
        assert actor_id is not None
        out.append(actor_id)
    return out


@pytest.mark.asyncio(loop_scope="function")
async def test_missing_actor_ids(repo: Repository, resource_store: ResourceStore) -> None:
    actor_id = await _actor(repo, "Alice", overview="bio")
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload(actor_ids=[actor_id, 999999]))

    assert result.success
    assert result.result is not None
    assert result.result.requested == 2
    assert result.result.missing == 1
    assert result.result.matched == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_unconfigured_server_fails(repo: Repository, resource_store: ResourceStore) -> None:
    handler = ActorPushHandler(repo, resource_store, HotSettings(), client_factory=lambda _: _FakeClient([]))
    result = await handler.handle(ActorPushPayload())

    assert not result.success
    assert result.error is not None
    assert "media_server" in result.error


@pytest.mark.asyncio(loop_scope="function")
async def test_partial_failure_stays_successful(repo: Repository, resource_store: ResourceStore) -> None:
    await _actor(repo, "Alice", overview="bio")
    await _actor(repo, "Bob", overview="bio")
    client = _FakeClient([_person("Alice"), _person("Bob")], fail_updates=frozenset({"Alice"}))

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.updated == 1
    assert result.result.failed == 1
    assert result.result.failures


@pytest.mark.asyncio(loop_scope="function")
async def test_all_failures_fail_task(repo: Repository, resource_store: ResourceStore) -> None:
    await _actor(repo, "Alice", overview="bio")
    client = _FakeClient([_person("Alice")], fail_updates=frozenset({"Alice"}))

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert not result.success
    assert result.result is not None
    assert result.result.updated == 0
    assert result.result.failed == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_person_list_failure_fails_task(repo: Repository, resource_store: ResourceStore) -> None:
    await _actor(repo, "Alice", overview="bio")
    client = _FakeClient([], fail_list=True)

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert not result.success
    assert result.error is not None
    assert "读取人物列表失败" in result.error
    assert result.result is not None
    assert result.result.requested == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_image_failure_stays_successful_with_metadata_update(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    image_url = await _image_url(resource_store, tmp_path)
    await _actor(repo, "Alice", overview="bio", image_urls=[image_url])
    client = _FakeClient([_person("Alice")], fail_images=frozenset({"p-Alice"}))

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.updated == 1
    assert result.result.images == 0
    assert result.result.failed == 1
    assert any("头像上传失败" in item for item in result.result.failures)


@pytest.mark.asyncio(loop_scope="function")
async def test_no_image_still_pushes_metadata(repo: Repository, resource_store: ResourceStore) -> None:
    await _actor(repo, "Alice", overview="bio")
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.no_image == 1
    assert result.result.updated == 1
    assert result.result.images == 0
