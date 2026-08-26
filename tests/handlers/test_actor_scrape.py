"""ACTOR_SCRAPE handler 单元测试."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from amane.config import ActorScrapingConfig, HotSettings
from amane.crawlers.actor import ActorFetcher, ActorMetadata
from amane.crawlers.block import FailureReason
from amane.db.models import Actor, FacetKind, Task, TaskStatus, TaskType
from amane.enums import ActorGender, SiteName
from amane.handlers.actor_scrape import ActorScrapeHandler
from amane.handlers.models import ActorScrapePayload, CacheKind
from amane.net.errors import SourceError
from amane.observability.models import SiteOutcomeKind
from amane.observability.recorder import Recorder

if TYPE_CHECKING:
    from amane.db.repository import Repository


class _FakeActorCrawler:
    def __init__(self, hits: dict[str, ActorMetadata | None]):
        self._hits = hits
        self.calls: list[str] = []

    async def fetch(self, name: str) -> ActorMetadata | None:
        self.calls.append(name)
        return self._hits.get(name)


class _FakeFactory:
    def __init__(self, crawlers: dict[str, ActorFetcher]):
        self._crawlers = crawlers

    async def get_actor_crawlers(self, names: Iterable[str]) -> dict[str, ActorFetcher]:
        return {n: self._crawlers[n] for n in names if n in self._crawlers}


@pytest.fixture
def hot() -> HotSettings:
    return HotSettings(
        actor_scraping=ActorScrapingConfig(
            profile_sites=[SiteName.MINNANO], image_sites=[SiteName.GFRIENDS], download_images=False
        )
    )


async def _actor_id(repo: Repository, name: str, *, gender: ActorGender = ActorGender.FEMALE) -> int:
    await repo.upsert_metadata(number=f"AS-{name}", actors=[name])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_id = next(a.id for a in actors if a.name == name)
    assert actor_id is not None
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    if actor.gender != gender:
        actor.gender = gender
        await repo.save_actor(actor)
    return actor_id


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_fills_empty_and_preserves_existing(repo: Repository, hot: HotSettings) -> None:
    actor_id = await _actor_id(repo, "Alice")

    existing = await repo.get_actor(actor_id)
    assert existing is not None
    existing.birthday = "1990-01-01"
    await repo.save_actor(existing)

    minnano = _FakeActorCrawler(
        {
            "Alice": ActorMetadata(
                name="Alice",
                birthday="2000-01-01",
                height=160,
                aliases=["ありす"],
                overview="from minnano",
            )
        }
    )
    gfriends = _FakeActorCrawler({"Alice": ActorMetadata(name="Alice", image_urls=["https://img.example/a.jpg"])})
    factory = _FakeFactory({"minnano": minnano, "gfriends": gfriends})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    assert result.result is not None
    assert result.result.image_count == 1
    assert "minnano" not in result.result.failed_sites
    assert "gfriends" not in result.result.failed_sites

    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.birthday == "1990-01-01"
    assert saved.height == 160
    assert saved.overview == "from minnano"
    assert saved.name == "Alice"
    assert await repo.get_actor_aliases(actor_id) == ["ありす"]
    assert saved.image_urls == ["https://img.example/a.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_tries_lookup_aliases(repo: Repository, hot: HotSettings) -> None:
    actor_id = await _actor_id(repo, "Canonical")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    await repo.save_actor(actor, aliases=["旧名"])

    crawler = _FakeActorCrawler(
        {
            "Canonical": None,
            "旧名": ActorMetadata(name="旧名", birthplace="Tokyo"),
        }
    )
    factory = _FakeFactory({"minnano": crawler, "gfriends": _FakeActorCrawler({})})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    assert crawler.calls == ["Canonical", "旧名"]
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.birthplace == "Tokyo"
    assert saved.name == "Canonical"
    assert await repo.get_actor_aliases(actor_id) == ["旧名"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_folds_site_display_name_into_aliases(repo: Repository, hot: HotSettings) -> None:
    """已认定规范名时, 站点显示名与其它写法进别名行, 不改 name."""
    actor_id = await _actor_id(repo, "鷲尾めい")
    minnano = _FakeActorCrawler({"鷲尾めい": ActorMetadata(name="筧純", aliases=["鷲尾芽衣", "筧ジュン", "鷲尾めい"])})
    factory = _FakeFactory({"minnano": minnano, "gfriends": _FakeActorCrawler({})})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.name == "鷲尾めい"
    assert await repo.get_actor_aliases(actor_id) == ["筧純", "鷲尾芽衣", "筧ジュン"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_missing_actor(repo: Repository, hot: HotSettings) -> None:
    handler = ActorScrapeHandler(repo, _FakeFactory({}), AsyncMock(), hot)
    result = await handler.handle(ActorScrapePayload(actor_id=99999))
    assert not result.success
    assert result.error is not None
    assert "not found" in result.error


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_reuses_raw_when_metadata_cache_enabled(repo: Repository, hot: HotSettings) -> None:
    actor_id = await _actor_id(repo, "Cached")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.raw = {
        "minnano": {
            "name": "Cached",
            "birthday": "1991-02-03",
            "height": 155,
            "overview": "from raw",
        },
        "gfriends": {
            "name": "Cached",
            "image_urls": ["https://img.example/cached.jpg"],
        },
    }
    await repo.save_actor(actor)

    minnano = _FakeActorCrawler({})
    gfriends = _FakeActorCrawler({})
    factory = _FakeFactory({"minnano": minnano, "gfriends": gfriends})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(
        ActorScrapePayload(actor_id=actor_id, use_cache={CacheKind.metadata, CacheKind.trans})
    )
    assert result.success
    assert minnano.calls == []
    assert gfriends.calls == []
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.birthday == "1991-02-03"
    assert saved.height == 155
    assert saved.overview == "from raw"
    assert saved.image_urls == ["https://img.example/cached.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_bypasses_raw_when_use_cache_empty(repo: Repository, hot: HotSettings) -> None:
    actor_id = await _actor_id(repo, "Forced")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.raw = {
        "minnano": {"name": "Forced", "birthday": "1980-01-01", "overview": "stale"},
    }
    await repo.save_actor(actor)

    minnano = _FakeActorCrawler({"Forced": ActorMetadata(name="Forced", birthday="2001-01-01", overview="fresh")})
    gfriends = _FakeActorCrawler({"Forced": ActorMetadata(name="Forced", image_urls=["https://img.example/new.jpg"])})
    factory = _FakeFactory({"minnano": minnano, "gfriends": gfriends})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
    assert result.success
    assert minnano.calls == ["Forced"]
    assert gfriends.calls == ["Forced"]
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.birthday == "2001-01-01"
    assert saved.overview == "fresh"
    assert saved.image_urls == ["https://img.example/new.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_invalid_raw_falls_through_to_fetch(repo: Repository, hot: HotSettings) -> None:
    actor_id = await _actor_id(repo, "BadRaw")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.raw = {"minnano": {"height": "not-an-int"}}
    await repo.save_actor(actor)

    minnano = _FakeActorCrawler({"BadRaw": ActorMetadata(name="BadRaw", height=170)})
    factory = _FakeFactory({"minnano": minnano, "gfriends": _FakeActorCrawler({})})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache={CacheKind.metadata}))
    assert result.success
    assert minnano.calls == ["BadRaw"]
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.height == 170


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_male_skips_female_only_sites_and_raw(repo: Repository, hot: HotSettings) -> None:
    hot.actor_scraping.profile_sites = [SiteName.MINNANO, SiteName.WIKIPEDIA]
    hot.actor_scraping.image_sites = [SiteName.GFRIENDS]
    actor_id = await _actor_id(repo, "MaleActor", gender=ActorGender.MALE)
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.raw = {
        "minnano": {"name": "MaleActor", "birthday": "1988-01-01", "overview": "wrong woman"},
        "wikipedia": {"name": "MaleActor", "overview": "from wiki raw"},
    }
    await repo.save_actor(actor)

    minnano = _FakeActorCrawler({"MaleActor": ActorMetadata(name="MaleActor", overview="minnano hit")})
    wiki = _FakeActorCrawler({})
    gfriends = _FakeActorCrawler({"MaleActor": ActorMetadata(name="MaleActor", image_urls=["https://x/y.jpg"])})
    factory = _FakeFactory({"minnano": minnano, "wikipedia": wiki, "gfriends": gfriends})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache={CacheKind.metadata}))
    assert result.success
    assert minnano.calls == []
    assert gfriends.calls == []
    assert wiki.calls == []  # cache hit on wikipedia only
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.overview == "from wiki raw"
    assert saved.birthday is None  # minnano raw not applied
    assert saved.image_urls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_unknown_skips_female_only_like_male(repo: Repository, hot: HotSettings) -> None:
    hot.actor_scraping.profile_sites = [SiteName.MINNANO, SiteName.WIKIPEDIA]
    hot.actor_scraping.image_sites = [SiteName.GFRIENDS]
    actor_id = await _actor_id(repo, "Unk", gender=ActorGender.UNKNOWN)

    minnano = _FakeActorCrawler({"Unk": ActorMetadata(name="Unk", birthday="1990-01-01")})
    wiki = _FakeActorCrawler({"Unk": ActorMetadata(name="Unk", gender=ActorGender.MALE, overview="wiki")})
    gfriends = _FakeActorCrawler({"Unk": ActorMetadata(name="Unk", image_urls=["https://x/y.jpg"])})
    factory = _FakeFactory({"minnano": minnano, "wikipedia": wiki, "gfriends": gfriends})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
    assert result.success
    assert minnano.calls == []
    assert gfriends.calls == []
    assert wiki.calls == ["Unk"]
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.gender == ActorGender.MALE
    assert saved.overview == "wiki"
    assert saved.birthday is None
    assert saved.image_urls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_row_roundtrip(repo: Repository) -> None:
    actor_id = await _actor_id(repo, "Bob")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.cup = "C"
    actor.image_urls = ["https://x/y.jpg"]
    saved = await repo.save_actor(actor)
    assert saved is not None
    assert saved.cup == "C"
    assert saved.image_urls == ["https://x/y.jpg"]
    assert isinstance(saved, Actor)


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_writes_task_summary(repo: Repository, hot: HotSettings, tmp_path: Path) -> None:
    """演员刮削补写 summary: 站点结果 (含失败原因) 进同一结构化出口."""
    actor_id = await _actor_id(repo, "Summarized")
    hot.actor_scraping.profile_sites = [SiteName.MINNANO]
    hot.actor_scraping.image_sites = [SiteName.GFRIENDS]

    minnano = _FakeActorCrawler({"Summarized": ActorMetadata(name="Summarized")})
    gfriends = _FakeActorCrawler({})  # 命中但无头像 → 失败
    handler = ActorScrapeHandler(repo, _FakeFactory({"minnano": minnano, "gfriends": gfriends}), AsyncMock(), hot)

    task = Task(id=71, type=TaskType.ACTOR_SCRAPE, status=TaskStatus.RUNNING, payload={"actor_id": actor_id})
    rec = Recorder.begin(tmp_path, task, hot)
    try:
        result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
        assert result.success
        assert rec.summary.sites_queried == ["minnano", "gfriends"]
        assert rec.summary.outcomes["minnano"].outcome == SiteOutcomeKind.OK
        assert rec.summary.outcomes["gfriends"].outcome == SiteOutcomeKind.FAILED
        assert rec.summary.outcomes["gfriends"].reason == FailureReason.NO_USABLE_METADATA
    finally:
        rec.close()


class _RaisingActorCrawler:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls: list[str] = []

    async def fetch(self, name: str) -> ActorMetadata | None:
        self.calls.append(name)
        raise self._exc


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_source_error_records_reason(repo: Repository, hot: HotSettings, tmp_path: Path) -> None:
    actor_id = await _actor_id(repo, "Blocked")
    hot.actor_scraping.profile_sites = [SiteName.MINNANO]
    hot.actor_scraping.image_sites = []
    crawler = _RaisingActorCrawler(SourceError(FailureReason.IP_BANNED, http_status=403, detail="banned"))
    handler = ActorScrapeHandler(repo, _FakeFactory({"minnano": crawler}), AsyncMock(), hot)

    task = Task(id=72, type=TaskType.ACTOR_SCRAPE, status=TaskStatus.RUNNING, payload={"actor_id": actor_id})
    rec = Recorder.begin(tmp_path, task, hot)
    try:
        result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
        assert result.success
        assert rec.summary.outcomes["minnano"].reason == FailureReason.IP_BANNED
        assert rec.summary.outcomes["minnano"].http_status == 403
        assert crawler.calls  # tried at least one lookup name
    finally:
        rec.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_unexpected_records_unexpected(repo: Repository, hot: HotSettings, tmp_path: Path) -> None:
    actor_id = await _actor_id(repo, "Buggy")
    hot.actor_scraping.profile_sites = [SiteName.MINNANO]
    hot.actor_scraping.image_sites = []
    handler = ActorScrapeHandler(
        repo, _FakeFactory({"minnano": _RaisingActorCrawler(RuntimeError("boom"))}), AsyncMock(), hot
    )

    task = Task(id=73, type=TaskType.ACTOR_SCRAPE, status=TaskStatus.RUNNING, payload={"actor_id": actor_id})
    rec = Recorder.begin(tmp_path, task, hot)
    try:
        result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
        assert result.success
        assert rec.summary.outcomes["minnano"].reason == FailureReason.UNEXPECTED
    finally:
        rec.close()
