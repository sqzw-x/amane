"""ACTOR_SCRAPE handler 单元测试."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from amane.config import ActorScrapingConfig, HotSettings
from amane.crawlers.actor import ActorFetcher, ActorMetadata
from amane.crawlers.block import FailureReason
from amane.db.models import FacetKind, Task, TaskStatus, TaskType
from amane.enums import ActorField, ActorGender, SiteName
from amane.handlers.actor_scrape import ActorScrapeHandler
from amane.handlers.models import ActorScrapePayload, CacheKind
from amane.media import ResourceStore
from amane.media.resource_store import RESOURCE_URL_PREFIX
from amane.net.errors import SourceError
from amane.observability.models import SiteOutcomeKind
from amane.observability.recorder import Recorder

if TYPE_CHECKING:
    from amane.db.repository import Repository
    from amane.net.http import WebClient


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
async def test_actor_scrape_prefers_fetched_values_and_fills_empty(repo: Repository, hot: HotSettings) -> None:
    """本次结果覆盖库内已填值; 库内空位照填."""
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
    assert saved.birthday == "2000-01-01"
    assert saved.height == 160
    assert saved.overview == "from minnano"
    assert saved.name == "Alice"
    assert await repo.get_actor_aliases(actor_id) == ["ありす"]
    assert saved.image_urls == ["https://img.example/a.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_keeps_images_when_run_yields_none(repo: Repository, hot: HotSettings) -> None:
    """本次抓取无图时保留库内头像 (有图即替换由纯函数表覆盖)."""
    actor_id = await _actor_id(repo, "Imaged")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.image_urls = ["https://img.example/old.jpg"]
    await repo.save_actor(actor)

    factory = _FakeFactory(
        {"minnano": _FakeActorCrawler({"Imaged": ActorMetadata(name="Imaged")}), "gfriends": _FakeActorCrawler({})}
    )
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.image_urls == ["https://img.example/old.jpg"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_skips_locked_aliases(repo: Repository, hot: HotSettings) -> None:
    """别名已锁: 站点别名不写入, 库内行原样保留, 其余字段照常写回."""
    actor_id = await _actor_id(repo, "FrozenAlias")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    await repo.save_actor(actor, aliases=["手填别名"])
    await repo.set_actor_locks(actor_id, [ActorField.ALIASES])

    minnano = _FakeActorCrawler({"FrozenAlias": ActorMetadata(name="站点显示名", aliases=["站点别名"], overview="bio")})
    factory = _FakeFactory({"minnano": minnano, "gfriends": _FakeActorCrawler({})})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.overview == "bio"  # 锁定别名不影响其余字段写回
    # #354 的回归点: 站点别名与既有别名行都不写回.
    assert await repo.get_actor_aliases(actor_id) == ["手填别名"]


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_respects_locked_fields(repo: Repository, hot: HotSettings) -> None:
    """锁定字段不被填 / 不被并集; 下载与任务结果同以过滤后集合为准."""
    actor_id = await _actor_id(repo, "LockedActor")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.birthday = "1990-01-01"
    await repo.save_actor(actor)
    await repo.set_actor_locks(actor_id, [ActorField.BIRTHDAY, ActorField.IMAGE_URLS])

    minnano = _FakeActorCrawler({"LockedActor": ActorMetadata(name="LockedActor", birthday="2000-01-01", height=160)})
    gfriends = _FakeActorCrawler(
        {"LockedActor": ActorMetadata(name="LockedActor", image_urls=["https://img.example/a.jpg"])}
    )
    factory = _FakeFactory({"minnano": minnano, "gfriends": gfriends})
    hot.actor_scraping.download_images = True
    resource_store = AsyncMock()
    handler = ActorScrapeHandler(repo, factory, resource_store, hot, web_client=cast("WebClient", AsyncMock()))

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))

    assert result.success
    assert result.result is not None
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.birthday == "1990-01-01"  # 锁定字段压住本次的新值
    assert saved.height == 160  # 未锁定字段取本次结果
    assert saved.image_urls == []  # 锁定 image_urls 不被并集
    assert result.result.image_count == 0
    assert "birthday" not in result.result.field_sources
    assert "image_urls" not in result.result.field_sources
    assert result.result.field_sources == saved.field_sources
    # 注定丢弃的新图不下载.
    resource_store.acquire.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_internal_avatar_needs_no_request(
    repo: Repository, resource_store: ResourceStore, hot: HotSettings
) -> None:
    """主图为内部 URL 时 download_images 直接解析本地文件, 不触发任何请求."""
    hot.actor_scraping.download_images = True
    actor_id = await _actor_id(repo, "Cropped")

    async def producer(dest: Path) -> bool:
        dest.write_bytes(b"avatar-bytes")
        return True

    res = await resource_store.acquire_derived("https://img.example/orig.jpg", "crop", "box:0,0,10,10", producer)
    assert res is not None
    internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(res.url)}"
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.image_urls = [internal]
    await repo.save_actor(actor)

    class _ExplodingClient:
        async def download(self, url: str, dest: Path, **kwargs: object) -> bool:
            raise AssertionError("内部 URL 不应触网")

        async def resolve_final_url(self, url: str, **kwargs: object) -> str:
            raise AssertionError("内部 URL 不应触网")

    factory = _FakeFactory(
        {
            "minnano": _FakeActorCrawler({"Cropped": ActorMetadata(name="Cropped")}),
            "gfriends": _FakeActorCrawler({"Cropped": ActorMetadata(name="Cropped")}),
        }
    )
    handler = ActorScrapeHandler(repo, factory, resource_store, hot, web_client=cast("WebClient", _ExplodingClient()))

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))

    assert result.success
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.image_urls == [internal]  # 内部 URL 保持主图位置


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
    assert "不存在" in result.error


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
    """强制刷新: 全站重爬, 站点快照按本次结果覆盖, 未参与站点的快照保留."""
    actor_id = await _actor_id(repo, "Forced")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.birthday = "1980-01-01"
    actor.raw = {
        "minnano": {"name": "Forced", "birthday": "1980-01-01", "overview": "stale"},
        "wikipedia": {"name": "Forced", "overview": "kept"},
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
    assert saved.raw["minnano"]["birthday"] == "2001-01-01"
    assert saved.raw["minnano"]["overview"] == "fresh"
    assert saved.raw["wikipedia"] == {"name": "Forced", "overview": "kept"}


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_scrape_keeps_stored_values_when_all_sites_miss(repo: Repository, hot: HotSettings) -> None:
    """站点参与但全部未命中: 库内标量, 头像与旧快照都保留 (整轮空结果不得清库)."""
    actor_id = await _actor_id(repo, "Missed")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.overview = "库内简介"
    actor.image_urls = ["https://img.example/old.jpg"]
    actor.raw = {"minnano": {"name": "Missed", "overview": "旧快照"}}
    await repo.save_actor(actor)

    factory = _FakeFactory({"minnano": _FakeActorCrawler({}), "gfriends": _FakeActorCrawler({})})
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None)

    result = await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=set()))
    assert result.success
    assert result.result is not None
    assert set(result.result.failed_sites) == {"minnano", "gfriends"}
    saved = await repo.get_actor(actor_id)
    assert saved is not None
    assert saved.overview == "库内简介"
    assert saved.image_urls == ["https://img.example/old.jpg"]
    assert saved.raw == {"minnano": {"name": "Missed", "overview": "旧快照"}}


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
    assert saved.raw["minnano"]["birthday"] == "1988-01-01"  # 被裁站点的快照保留
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
async def test_actor_scrape_writes_task_summary(repo: Repository, hot: HotSettings, tmp_path: Path) -> None:
    """演员刮削补写 summary: 站点结果 (含失败原因) 进同一结构化导出."""
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


class _RecordingTranslator:
    """记录调用并按注入结果作答的翻译面替身."""

    def __init__(self, result: str | None = "译文", *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, str, str, bool]] = []

    async def translate(self, text, target, field, *, use_cache: bool = True):
        self.calls.append((text, str(target), str(field), use_cache))
        if self._raises:
            raise RuntimeError("llm down")
        return self._result


async def _scrape_with_overview(repo: Repository, hot: HotSettings, overview: str, translator: object | None) -> int:
    actor_id = await _actor_id(repo, "Alice")
    factory = _FakeFactory(
        {
            "minnano": _FakeActorCrawler({"Alice": ActorMetadata(name="Alice", overview=overview)}),
            "gfriends": _FakeActorCrawler({"Alice": ActorMetadata(name="Alice")}),
        }
    )
    handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None, translator=translator)  # type: ignore[arg-type]
    result = await handler.handle(ActorScrapePayload(actor_id=actor_id))
    assert result.success
    return actor_id


class TestActorScrapeTranslation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_translates_overview(self, repo: Repository, hot: HotSettings) -> None:
        translator = _RecordingTranslator("似鸟是日本女演员.")
        actor_id = await _scrape_with_overview(repo, hot, "似鳥は日本の女優。", translator)

        assert [(text, target, field, use_cache) for text, target, field, use_cache in translator.calls] == [
            ("似鳥は日本の女優。", "zh_cn", "overview", True)
        ]
        saved = await repo.get_actor(actor_id)
        assert saved is not None
        assert saved.overview == "似鸟是日本女演员."

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_field_not_configured(self, repo: Repository, hot: HotSettings) -> None:
        hot.llm.actor_translate_fields = []
        translator = _RecordingTranslator()
        actor_id = await _scrape_with_overview(repo, hot, "似鳥は日本の女優。", translator)

        assert translator.calls == []
        saved = await repo.get_actor(actor_id)
        assert saved is not None
        assert saved.overview == "似鳥は日本の女優。"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_original_on_failure(self, repo: Repository, hot: HotSettings) -> None:
        translator = _RecordingTranslator(raises=True)
        actor_id = await _scrape_with_overview(repo, hot, "似鳥は日本の女優。", translator)

        saved = await repo.get_actor(actor_id)
        assert saved is not None
        assert saved.overview == "似鳥は日本の女優。"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_locked_overview_is_not_translated(self, repo: Repository, hot: HotSettings) -> None:
        actor_id = await _actor_id(repo, "Alice")
        existing = await repo.get_actor(actor_id)
        assert existing is not None
        existing.overview = "库内锁定值"
        await repo.save_actor(existing)
        await repo.set_actor_locks(actor_id, {ActorField.OVERVIEW})
        translator = _RecordingTranslator()
        factory = _FakeFactory(
            {
                "minnano": _FakeActorCrawler({"Alice": ActorMetadata(name="Alice", overview="似鳥は日本の女優。")}),
                "gfriends": _FakeActorCrawler({"Alice": ActorMetadata(name="Alice")}),
            }
        )
        handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None, translator=translator)  # type: ignore[arg-type]

        await handler.handle(ActorScrapePayload(actor_id=actor_id))

        assert translator.calls == []
        saved = await repo.get_actor(actor_id)
        assert saved is not None
        assert saved.overview == "库内锁定值"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_without_translator_keeps_original(self, repo: Repository, hot: HotSettings) -> None:
        actor_id = await _scrape_with_overview(repo, hot, "似鳥は日本の女優。", None)

        saved = await repo.get_actor(actor_id)
        assert saved is not None
        assert saved.overview == "似鳥は日本の女優。"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_translation_cache_follows_use_cache(self, repo: Repository, hot: HotSettings) -> None:
        """use_cache 不含 trans 时强制重译 (跳过缓存读取), 仍以 use_cache=False 调翻译面."""
        for use_cache, expected in (({CacheKind.metadata, CacheKind.trans}, True), (set(), False)):
            translator = _RecordingTranslator()
            actor_id = await _actor_id(repo, f"Alice{expected}")
            factory = _FakeFactory(
                {
                    "minnano": _FakeActorCrawler(
                        {f"Alice{expected}": ActorMetadata(name=f"Alice{expected}", overview="似鳥は日本の女優。")}
                    ),
                    "gfriends": _FakeActorCrawler({f"Alice{expected}": ActorMetadata(name=f"Alice{expected}")}),
                }
            )
            handler = ActorScrapeHandler(repo, factory, AsyncMock(), hot, web_client=None, translator=translator)  # type: ignore[arg-type]

            await handler.handle(ActorScrapePayload(actor_id=actor_id, use_cache=use_cache))

            assert translator.calls[0][3] is expected
