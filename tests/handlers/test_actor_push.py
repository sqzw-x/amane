"""ACTOR_PUSH handler: 补缺/覆盖, 匹配, 头像与失败语义."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import pytest

from amane.config import HotSettings, MediaServerConfig
from amane.db.repo_types import WriteMode
from amane.handlers import ActorPushHandler, ActorPushPayload
from amane.media import ResourceStore
from amane.media_server import SEARCH_LIMIT, MediaPerson, MediaServerError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path

    from amane.db.repository import Repository

    _SetMime = Callable[[str, str | None], Awaitable[None]]

_URL = "https://images.example/actor.jpg"
_BIN_URL = "https://images.example/actor"


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

    async def __aenter__(self) -> Self:
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


def _hot() -> HotSettings:
    return HotSettings(media_server=MediaServerConfig(url="http://media.local:8096", api_key="key"))


class _TruncatedSearchClient(_FakeClient):
    """模拟服务器在 50 条上限处截断模糊检索, 真正同名条目只在全量列表中."""

    async def search_persons(self, term: str) -> list[MediaPerson]:
        self.search_calls.append(term)
        return [MediaPerson(id=f"noise-{i}", name=f"Alice {i}") for i in range(SEARCH_LIMIT)]


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
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    resource = await store.acquire_derived(_URL, "crop", "actor-push-test", producer, ext=".png")
    assert resource is not None
    return resource.url


async def _bin_image_url(store: ResourceStore, set_mime: _SetMime, data: bytes, *, args: str) -> str:
    """造存量行: 先按正常入库写入, 再改写成改造前遗留的通用二进制形态."""

    async def producer(dest: Path) -> bool:
        dest.write_bytes(data)
        return True

    resource = await store.acquire_derived(_BIN_URL, "crop", args, producer, ext=".bin")
    assert resource is not None
    await set_mime(resource.url, "application/octet-stream")
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
    await _actor(
        repo,
        "Alice",
        overview="new bio",
        birthday="1990-01-01",
        birthplace="Tokyo",
        image_urls=[image_url],
    )
    await _actor(repo, "Bob", overview="bob bio")
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


@pytest.mark.asyncio(loop_scope="function")
async def test_recorded_type_is_not_used_for_push(
    repo: Repository, resource_store: ResourceStore, set_resource_mime: _SetMime
) -> None:
    """旧行标成图片而内容不是图片时不发送: 记录里的媒体类型不参与判定."""

    async def producer(dest: Path) -> bool:
        dest.write_bytes(b"<html>blocked</html>")
        return True

    resource = await resource_store.acquire_derived(_URL, "crop", "recorded-type", producer, ext=".jpg")
    assert resource is not None
    await set_resource_mime(resource.url, "image/jpeg")
    await _actor(repo, "Alice", image_urls=[resource.url])
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert not result.success
    assert result.result is not None
    assert client.images == []
    assert result.result.images == 0
    assert result.result.no_image == 0
    assert result.result.failed == 1
    assert result.result.failures == [f"Alice / Alice: 头像格式无法识别: {resource.url}"]


@pytest.mark.asyncio(loop_scope="function")
async def test_uploads_octet_stream_avatar_by_content(
    repo: Repository, resource_store: ResourceStore, set_resource_mime: _SetMime
) -> None:
    """存量记录类型未定: 无后缀头像按内容判定后上传, 不能把 octet-stream 发给服务器."""
    image_url = await _bin_image_url(resource_store, set_resource_mime, b"\x89PNG\r\n\x1a\n", args="octet-png")
    await _actor(repo, "Alice", image_urls=[image_url])
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.images == 1
    assert result.result.failed == 0
    assert client.images == [("p-Alice", "image/png")]


@pytest.mark.asyncio(loop_scope="function")
async def test_magic_miss_falls_back_to_pil(
    repo: Repository, resource_store: ResourceStore, set_resource_mime: _SetMime, bmp_bytes: bytes
) -> None:
    """文件头未登记的格式由 PIL 判定."""
    image_url = await _bin_image_url(resource_store, set_resource_mime, bmp_bytes, args="octet-bmp")
    await _actor(repo, "Alice", image_urls=[image_url])
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.result is not None
    assert result.result.images == 1
    assert client.images == [("p-Alice", "image/bmp")]


@pytest.mark.asyncio(loop_scope="function")
async def test_skips_unrecognized_avatar_and_tries_next(
    repo: Repository, resource_store: ResourceStore, set_resource_mime: _SetMime
) -> None:
    """识别不出格式的头像不发送, 继续试下一个 URL."""
    blocked = await _bin_image_url(resource_store, set_resource_mime, b"<html>blocked</html>", args="blocked")
    valid = await _bin_image_url(resource_store, set_resource_mime, b"\xff\xd8\xff\xe0", args="valid")
    await _actor(repo, "Alice", image_urls=[blocked, valid])
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.result is not None
    assert result.result.images == 1
    assert result.result.failed == 0
    assert client.images == [("p-Alice", "image/jpeg")]


@pytest.mark.asyncio(loop_scope="function")
async def test_fails_without_sending_unrecognized_avatar(
    repo: Repository, resource_store: ResourceStore, set_resource_mime: _SetMime
) -> None:
    """落盘资源识别不出图片时不发起上传: 服务器只会以类型不明拒绝."""
    image_url = await _bin_image_url(resource_store, set_resource_mime, b"<html>blocked</html>", args="only")
    await _actor(repo, "Alice", image_urls=[image_url])
    client = _FakeClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert not result.success
    assert result.result is not None
    assert client.images == []
    assert result.result.images == 0
    assert result.result.no_image == 0
    assert result.result.failed == 1
    assert result.result.failures == [f"Alice / Alice: 头像格式无法识别: {image_url}"]


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
async def test_search_limit_falls_back_to_full_index(repo: Repository, resource_store: ResourceStore) -> None:
    actor_id = await _actor(repo, "Alice", overview="bio")
    client = _TruncatedSearchClient([_person("Alice")])

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload(actor_ids=[actor_id]))

    assert result.success
    assert result.result is not None
    assert result.result.matched == 1
    assert result.result.updated == 1
    assert client.search_calls == ["Alice"]
    assert client.list_calls == 1


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


@pytest.mark.parametrize(
    ("names", "fail_updates", "fail_images", "success", "updated", "images", "failed", "failure"),
    [
        (("Alice", "Bob"), frozenset({"Alice"}), frozenset(), True, 1, 0, 1, None),
        (("Alice",), frozenset({"Alice"}), frozenset(), False, 0, 0, 1, None),
        (("Alice",), frozenset(), frozenset({"p-Alice"}), True, 1, 0, 1, "头像上传失败"),
    ],
    ids=["partial-update", "all-updates", "image-after-update"],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_failure_outcomes(
    repo: Repository,
    resource_store: ResourceStore,
    tmp_path: Path,
    names: tuple[str, ...],
    fail_updates: frozenset[str],
    fail_images: frozenset[str],
    success: bool,
    updated: int,
    images: int,
    failed: int,
    failure: str | None,
) -> None:
    """部分失败仍算成功, 全部失败则任务失败; 字段写入成功后头像失败不回滚字段."""
    image_url = await _image_url(resource_store, tmp_path) if fail_images else None
    persons = []
    for name in names:
        await _actor(repo, name, overview="bio", image_urls=[image_url] if image_url else [])
        persons.append(_person(name))
    client = _FakeClient(persons, fail_updates=fail_updates, fail_images=fail_images)

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success is success
    assert result.result is not None
    assert result.result.updated == updated
    assert result.result.images == images
    assert result.result.failed == failed
    if failure is not None:
        assert any(failure in item for item in result.result.failures)


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
async def test_same_name_persons_all_update_once(repo: Repository, resource_store: ResourceStore) -> None:
    """同名人物全部更新, 同一人物在一次任务中只处理一次."""
    await _actor(repo, "Alice", overview="bio")
    client = _FakeClient(
        [_person("Alice", person_id="p1"), _person("Alice", person_id="p1"), _person("Alice", person_id="p2")]
    )

    handler = ActorPushHandler(repo, resource_store, _hot(), client_factory=lambda _: client)
    result = await handler.handle(ActorPushPayload())

    assert result.success
    assert result.result is not None
    assert result.result.matched == 2
    assert result.result.updated == 2
    assert [call[0] for call in client.updates] == ["p1", "p2"]
