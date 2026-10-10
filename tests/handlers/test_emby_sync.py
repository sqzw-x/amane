"""EmbySyncHandler: 匹配, 幂等与失败语义; 以及演员刮削的后继挂接."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from amane.config import EmbyConfig, HotSettings
from amane.db.models import Actor, FacetKind
from amane.emby import EmbyError, EmbyPerson, normalize_person_name
from amane.handlers import ActorScrapeHandler, EmbySyncHandler
from amane.handlers.models import ActorScrapePayload, EmbySyncPayload

if TYPE_CHECKING:
    from amane.db import Repository
    from amane.media import ResourceStore

_PORTRAIT_URL = "https://img.example/alice.jpg"
_PORTRAIT_BYTES = b"jpeg-bytes"


class _FakeEmby:
    """替代 EmbyClient: 记录出站动作, 按脚本返回人物条目."""

    def __init__(self) -> None:
        self.persons: list[EmbyPerson] = []
        self.list_error: EmbyError | None = None
        self.upload_errors: dict[str, str] = {}
        self.fail_update_ids: set[str] = set()
        self.uploaded: list[tuple[str, bytes, str]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.list_calls = 0
        self.search_calls: list[str] = []

    async def __aenter__(self) -> _FakeEmby:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def list_persons(self) -> list[EmbyPerson]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.persons)

    async def search_persons(self, term: str, *, limit: int = 50) -> list[EmbyPerson]:
        """服务器的检索是模糊的: 这里按子串返回候选, 精确匹配由 handler 完成."""
        self.search_calls.append(term)
        needle = normalize_person_name(term)
        return [person for person in self.persons if needle and needle in normalize_person_name(person.name)]

    async def upload_primary_image(self, person_id: str, data: bytes, *, content_type: str) -> None:
        error = self.upload_errors.get(person_id)
        if error is not None:
            raise EmbyError(error)
        self.uploaded.append((person_id, data, content_type))

    async def update_person(self, person_id: str, fields: dict[str, Any]) -> None:
        if person_id in self.fail_update_ids:
            raise EmbyError("HTTP 500")
        self.updated.append((person_id, dict(fields)))


class _NoFactory:
    """ActorScrapeHandler 只需要一个能取爬虫的对象; 本文件不触发抓取."""

    async def get_actor_crawlers(self, names: Any) -> dict[str, Any]:
        return {}


def _hot(**emby: Any) -> HotSettings:
    return HotSettings(emby=EmbyConfig(url="http://emby.local:8096", api_key="key", **emby))


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeEmby) -> None:
    monkeypatch.setattr("amane.handlers.emby_sync.EmbyClient", lambda _config: fake)


async def _save_actor(
    repo: Repository,
    store: ResourceStore,
    name: str,
    *,
    portrait: bool = True,
    aliases: list[str] | None = None,
    overview: str | None = None,
    birthday: str | None = None,
    birthplace: str | None = None,
) -> Actor:
    """建一个演员; ``portrait`` 为真时落一份本地头像资源并挂在 ``image_urls`` 上.

    Actor 实体只能经聚合路径新建 (``save_actor`` 对无 id 的行返回 None), 因此先注册一部影片的演员.
    """
    await repo.upsert_metadata(number=f"EMBY-{name}", actors=[name])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_id = next(actor.id for actor in actors if actor.name == name)
    assert actor_id is not None
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.overview = overview
    actor.birthday = birthday
    actor.birthplace = birthplace
    if portrait:
        record = await _portrait_record(store)
        actor.image_urls = [record.url]
    saved = await repo.save_actor(actor, aliases=aliases)
    assert saved is not None
    return saved


async def _portrait_record(store: ResourceStore, url: str = _PORTRAIT_URL, data: bytes = _PORTRAIT_BYTES) -> Any:
    async def producer(dest: Any) -> bool:
        dest.write_bytes(data)
        return True

    record = await store.acquire_derived(url, "crop", "0.7", producer)
    assert record is not None
    return record


def _person(person_id: str = "p1", name: str = "Alice", **fields: Any) -> EmbyPerson:
    return EmbyPerson(id=person_id, name=name, has_primary_image=fields.pop("has_primary_image", False), **fields)


class TestConfiguration:
    @pytest.mark.asyncio
    async def test_unconfigured_task_fails(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        fake = _FakeEmby()
        _patch_client(monkeypatch, fake)
        handler = EmbySyncHandler(repo, resource_store, HotSettings())

        result = await handler.handle(EmbySyncPayload())

        assert result.success is False
        assert result.error is not None and "emby.api_key" in result.error

    @pytest.mark.asyncio
    async def test_missing_actor_fails(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        _patch_client(monkeypatch, _FakeEmby())
        handler = EmbySyncHandler(repo, resource_store, _hot())

        result = await handler.handle(EmbySyncPayload(actor_id=404))

        assert result.success is False
        assert result.error is not None and "404" in result.error


class TestSingleActor:
    @pytest.mark.asyncio
    async def test_uploads_portrait_and_fills_profile(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(
            repo, resource_store, "Alice", overview="bio", birthday="1990-01-02", birthplace="Osaka"
        )
        fake = _FakeEmby()
        fake.persons = [_person()]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        assert result.success is True
        payload = result.result
        assert payload is not None
        assert (payload.actors, payload.persons, payload.images, payload.updated) == (1, 1, 1, 1)
        assert fake.uploaded == [("p1", _PORTRAIT_BYTES, "image/jpeg")]
        # 默认模式不写生日: 它在 /Persons 的 fields 枚举之外, 读不回现值
        assert fake.updated == [("p1", {"Overview": "bio", "ProductionLocations": ["Osaka"]})]

    @pytest.mark.asyncio
    async def test_existing_values_are_kept_without_overwrite(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(repo, resource_store, "Alice", overview="bio", birthplace="Osaka")
        fake = _FakeEmby()
        fake.persons = [_person(has_primary_image=True, overview="server bio", production_locations=["Tokyo"])]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        assert fake.uploaded == [] and fake.updated == []
        assert result.result is not None and result.result.skipped == 1

    @pytest.mark.asyncio
    async def test_overwrite_writes_everything_including_birthday(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(
            repo, resource_store, "Alice", overview="bio", birthday="1990-01-02", birthplace="Osaka"
        )
        fake = _FakeEmby()
        fake.persons = [_person(has_primary_image=True, overview="server bio", premiere_date="1970-01-01")]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot(overwrite=True)).handle(
            EmbySyncPayload(actor_id=actor.id)
        )

        assert len(fake.uploaded) == 1
        assert fake.updated == [
            (
                "p1",
                {"Overview": "bio", "PremiereDate": "1990-01-02", "ProductionLocations": ["Osaka"]},
            )
        ]
        assert result.result is not None and result.result.images == 1

    @pytest.mark.asyncio
    async def test_force_beats_config(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        """payload.force 覆盖 emby.overwrite=false."""
        actor = await _save_actor(repo, resource_store, "Alice", birthday="1990-01-02")
        fake = _FakeEmby()
        fake.persons = [_person(has_primary_image=True)]
        _patch_client(monkeypatch, fake)

        await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id, force=True))

        assert len(fake.uploaded) == 1
        assert fake.updated == [("p1", {"PremiereDate": "1990-01-02"})]

    @pytest.mark.asyncio
    async def test_alias_matches_person(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        actor = await _save_actor(repo, resource_store, "Alice", aliases=["ありす"])
        fake = _FakeEmby()
        fake.persons = [_person(name="ありす"), _person("p2", "别人")]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        assert fake.search_calls == ["Alice", "ありす"]
        assert [call[0] for call in fake.uploaded] == ["p1"]
        assert result.result is not None and result.result.persons == 1

    @pytest.mark.asyncio
    async def test_person_hit_by_several_names_is_pushed_once(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        """别名与显示名只是同一人的不同写法时, 服务器条目仍只推一次, persons 计 1."""
        actor = await _save_actor(repo, resource_store, "Alice", aliases=["Ａｌｉｃｅ"])
        fake = _FakeEmby()
        fake.persons = [_person()]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        assert fake.search_calls == ["Alice", "Ａｌｉｃｅ"]
        assert len(fake.uploaded) == 1
        assert result.result is not None and result.result.persons == 1

    @pytest.mark.asyncio
    async def test_person_not_found_and_local_portrait_missing(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(repo, resource_store, "Alice", portrait=False)
        fake = _FakeEmby()
        fake.persons = []
        _patch_client(monkeypatch, fake)

        not_found = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))
        assert not_found.result is not None and not_found.result.not_found == 1

        fake.persons = [_person()]
        no_image = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))
        assert no_image.result is not None and no_image.result.no_image == 1
        assert fake.uploaded == []


class TestFailures:
    @pytest.mark.asyncio
    async def test_person_failure_is_recorded_and_others_continue(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(repo, resource_store, "Alice")
        fake = _FakeEmby()
        fake.persons = [_person("p1"), _person("p2")]
        fake.upload_errors["p1"] = "HTTP 500: boom"
        _patch_client(monkeypatch, fake)

        handler = EmbySyncHandler(repo, resource_store, _hot())
        result = await handler.handle(EmbySyncPayload(actor_id=actor.id))

        # 部分失败不改终态: 有一个条目写成功即算成功, 明细留在 failures
        assert result.success is True
        payload = result.result
        assert payload is not None
        assert payload.failed == 1 and payload.images == 1
        assert payload.failures == ["Alice: 头像上传失败: HTTP 500: boom"]

        # 取消或崩溃时补交已完成部分的快照
        snapshot = handler.failure_result(EmbySyncPayload(actor_id=actor.id))
        assert snapshot is not None
        assert (snapshot.actors, snapshot.images, snapshot.failed) == (1, 1, 1)

    @pytest.mark.asyncio
    async def test_all_writes_failing_fails_task(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        """一条都没写成且存在失败时整任务失败, 供用户重跑."""
        actor = await _save_actor(repo, resource_store, "Alice")
        fake = _FakeEmby()
        fake.persons = [_person("p1"), _person("p2")]
        fake.upload_errors.update({"p1": "HTTP 500", "p2": "HTTP 500"})
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        assert result.success is False
        assert result.result is not None and result.result.failed == 2

    @pytest.mark.asyncio
    async def test_list_failure_fails_whole_task(self, repo: Repository, resource_store: ResourceStore, monkeypatch):
        await _save_actor(repo, resource_store, "Alice")
        fake = _FakeEmby()
        fake.list_error = EmbyError("认证失败 (HTTP 401): emby.api_key 无效")
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload())

        assert result.success is False
        assert result.error is not None and "api_key" in result.error

    @pytest.mark.asyncio
    async def test_partial_failure_and_skipped_coexist(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        actor = await _save_actor(repo, resource_store, "Alice")
        fake = _FakeEmby()
        fake.persons = [
            _person("p1", has_primary_image=True),
            _person("p2"),
        ]
        fake.upload_errors["p2"] = "HTTP 500"
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id))

        payload = result.result
        assert payload is not None
        assert (payload.skipped, payload.failed, payload.images, payload.updated) == (1, 1, 0, 0)
        assert result.success is False  # 一条都没写成

    @pytest.mark.asyncio
    @pytest.mark.parametrize("birthday", ["1990-1-2", "1990", "1990/01/02", "不明"])
    async def test_invalid_birthday_not_pushed(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch, birthday: str
    ):
        actor = await _save_actor(repo, resource_store, "Alice", birthday=birthday)
        fake = _FakeEmby()
        fake.persons = [_person(has_primary_image=True)]
        _patch_client(monkeypatch, fake)

        await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=actor.id, force=True))

        assert fake.updated == []

    @pytest.mark.asyncio
    async def test_portrait_falls_through_to_next_url(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        """第一个头像记录的文件已经不在磁盘上时, 用下一个 URL 的记录."""
        actor = await _save_actor(repo, resource_store, "Alice", portrait=False)
        first = await _portrait_record(resource_store, "https://img.example/first.jpg")
        second = await _portrait_record(resource_store, "https://img.example/second.jpg", data=b"second-bytes")
        resource_store.full_path(first).unlink()
        actor.image_urls = [first.url, second.url]
        saved = await repo.save_actor(actor)
        assert saved is not None

        fake = _FakeEmby()
        fake.persons = [_person()]
        _patch_client(monkeypatch, fake)

        await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload(actor_id=saved.id))

        assert fake.uploaded == [("p1", b"second-bytes", "image/jpeg")]


class TestBulkSync:
    @pytest.mark.asyncio
    async def test_reads_person_list_once_and_matches_all_actors(
        self, repo: Repository, resource_store: ResourceStore, monkeypatch
    ):
        await _save_actor(repo, resource_store, "Alice")
        await _save_actor(repo, resource_store, "Bob", portrait=False)
        fake = _FakeEmby()
        fake.persons = [_person("p1", "Alice"), _person("p2", "Bob")]
        _patch_client(monkeypatch, fake)

        result = await EmbySyncHandler(repo, resource_store, _hot()).handle(EmbySyncPayload())

        assert fake.list_calls == 1
        assert fake.search_calls == []
        payload = result.result
        assert payload is not None
        assert (payload.actors, payload.persons, payload.images, payload.no_image) == (2, 2, 1, 1)


class TestActorScrapeFollowup:
    def _handler(self, repo: Repository, resource_store: ResourceStore, hot: HotSettings) -> ActorScrapeHandler:
        return ActorScrapeHandler(repo, _NoFactory(), resource_store, hot)

    def test_followup_added_when_enabled(self, repo: Repository, resource_store: ResourceStore):
        followups = self._handler(repo, resource_store, _hot())._emby_followups(ActorScrapePayload(actor_id=7))

        assert len(followups) == 1
        assert followups[0].key == "emby-sync:7"
        assert followups[0].task_type.value == "emby_sync"
        assert followups[0].payload == {"actor_id": 7, "force": False}
        assert followups[0].priority == -1

    @pytest.mark.parametrize(
        "hot",
        [HotSettings(), HotSettings(emby=EmbyConfig(url="http://emby.local:8096", api_key=None))],
    )
    def test_no_followup_without_server(self, repo: Repository, resource_store: ResourceStore, hot: HotSettings):
        assert self._handler(repo, resource_store, hot)._emby_followups(ActorScrapePayload(actor_id=7)) == []

    def test_no_followup_when_switch_off(self, repo: Repository, resource_store: ResourceStore):
        hot = _hot(sync_on_actor_scrape=False)

        assert self._handler(repo, resource_store, hot)._emby_followups(ActorScrapePayload(actor_id=7)) == []
