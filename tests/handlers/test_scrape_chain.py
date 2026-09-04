"""测试影片刮削后的链式演员刮削任务 (actor_scraping.auto_scrape)."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from amane.config import ActorScrapingConfig, HotSettings, ScrapingConfig
from amane.crawlers.base import Crawler
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.models import MediaMetadata
from amane.db.models import TaskType
from amane.enums import SiteName
from amane.handlers import ScrapeHandler, ScrapePayload
from amane.parsing import ContentType

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


class FakeCrawler(Crawler):
    @classmethod
    def profile(cls):
        from amane.crawlers.base import CrawlerProfile

        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://fake.example.com")

    def __init__(self, metadata: MediaMetadata):
        self._profile = self.profile()
        self.name = self._profile.name
        self._metadata = metadata

    async def _search(self, query, options=None) -> str | None:
        number = query.number if hasattr(query, "number") else query
        return f"https://fake.example.com/v/{number}"

    async def _scrape(self, url: str, options=None) -> MediaMetadata | None:
        return self._metadata


def _make_handler(repo, actors: list[str], *, auto_scrape: bool) -> ScrapeHandler:
    metadata = MediaMetadata.model_validate(
        {"number": "MIDV-123", "title": "Test Title", "actors": actors, "studio": "Studio X"}
    )
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {"javdb": FakeCrawler(metadata)}
    cfg = HotSettings(
        scraping=ScrapingConfig(field_priority={}),
        actor_scraping=ActorScrapingConfig(auto_scrape=auto_scrape, download_images=False),
    )
    return ScrapeHandler(repo, factory, AsyncMock(), cfg)


async def _list_actor_tasks(repo) -> list:
    return await repo.list_tasks(task_types=[TaskType.ACTOR_SCRAPE])


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_enqueues_actor_tasks_for_unscraped_actors(repo: Repository, tmp_path: Path):
    """auto_scrape 开启时, 影片演员 (未刮过) 各描述一个 ACTOR_SCRAPE followup, priority=-1."""
    handler = _make_handler(repo, ["Actor A", "Actor B"], auto_scrape=True)
    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.followups is not None

    actor_scrapes = [f for f in result.followups if f.task_type == TaskType.ACTOR_SCRAPE]
    assert len(actor_scrapes) == 2
    actors = {a.name: a for a in await repo.get_actors_by_names(["Actor A", "Actor B"])}
    payload_ids = {f.payload["actor_id"] for f in actor_scrapes}
    assert payload_ids == {a.id for a in actors.values()}
    assert all(f.priority == -1 for f in actor_scrapes)
    assert {f.key for f in actor_scrapes} == {f"actor-scrape:{a.id}" for a in actors.values()}


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_skips_actors_with_raw(repo: Repository, tmp_path: Path):
    """已刮过的 Actor (raw 非空) 不进入 followups; 未刮过的照常描述."""
    handler = _make_handler(repo, ["Actor A", "Actor B"], auto_scrape=True)
    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.followups is not None

    actor_a = (await repo.get_actors_by_names(["Actor A"]))[0]
    assert actor_a.id is not None
    await repo.update_actor(actor_a.id, raw={"minnano": {"gender": "female"}})

    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    actor_scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.ACTOR_SCRAPE]
    assert len(actor_scrapes) == 1
    actor_b = (await repo.get_actors_by_names(["Actor B"]))[0]
    assert actor_b.id is not None
    assert actor_scrapes[0].payload["actor_id"] == actor_b.id
    assert set(actor_scrapes[0].payload["use_cache"]) == {"metadata", "trans"}


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_skips_all_when_all_scraped(repo: Repository, tmp_path: Path):
    """全部演员已刮过 → 无 ACTOR_SCRAPE followup (幂等, 重刮影片零额外任务)."""
    handler = _make_handler(repo, ["Actor A", "Actor B"], auto_scrape=True)
    await handler.handle(ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED))
    for actor in await repo.get_actors_by_names(["Actor A", "Actor B"]):
        assert actor.id is not None
        await repo.update_actor(actor.id, raw={"minnano": {"gender": "female"}})

    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    actor_scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.ACTOR_SCRAPE]
    assert actor_scrapes == []


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_no_chain_when_disabled(repo: Repository, tmp_path: Path):
    """auto_scrape=False → 无 ACTOR_SCRAPE followup."""
    handler = _make_handler(repo, ["Actor A"], auto_scrape=False)
    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert [f for f in (result.followups or []) if f.task_type == TaskType.ACTOR_SCRAPE] == []


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_no_chain_without_actors(repo: Repository, tmp_path: Path):
    """影片无演员 → 无 ACTOR_SCRAPE followup."""
    handler = _make_handler(repo, [], auto_scrape=True)
    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.followups == []


@pytest.mark.asyncio(loop_scope="function")
async def test_chain_failure_does_not_fail_scrape(repo: Repository, tmp_path: Path, monkeypatch):
    """链式描述异常被吞掉, 不阻断刮削主流程 (机会主义)."""

    async def _boom(names):
        raise RuntimeError("enqueue boom")

    monkeypatch.setattr(repo, "get_actors_by_names", _boom)
    handler = _make_handler(repo, ["Actor A"], auto_scrape=True)
    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.followups == []


@pytest.mark.asyncio(loop_scope="function")
async def test_second_scrape_reuses_active_actor_task(repo: Repository, tmp_path: Path):
    """同演员已有 queued ACTOR_SCRAPE 时, 完成事务创建 followup 时复用, 不建第二条.

    复用语义在 _insert_or_reuse (队列互斥): 两个 SCRAPE 各返回同名同演员 followup,
    经 complete_task_with_followups 落库后仍只有一条 ACTOR_SCRAPE 任务.
    """
    from amane.scheduler.worker import AsyncWorker

    handler = _make_handler(repo, ["Actor A"], auto_scrape=True)
    result_a = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result_a.success is True and result_a.followups is not None

    # 另一部影片再刮一次, 得到同演员 followup
    result_b = await handler.handle(
        ScrapePayload(number="ABC-456", media_file_id=None, content_type=ContentType.CENSORED)
    )
    assert result_b.success is True and result_b.followups is not None

    worker = AsyncWorker(repo=repo, handlers={}, poll_interval=0.05)
    t1 = await repo.create_task(TaskType.SCRAPE, payload={"number": "MIDV-123"})
    t2 = await repo.create_task(TaskType.SCRAPE, payload={"number": "ABC-456"})
    assert t1.id is not None and t2.id is not None
    claimed1 = await repo.claim_next_task()
    claimed2 = await repo.claim_next_task()
    assert claimed1 is not None and claimed2 is not None
    assert claimed1.id is not None and claimed2.id is not None
    await repo.complete_task_with_followups(
        claimed1.id,
        result={"metadata_id": 1},
        followups=[(f.key, f.task_type, f.payload, f.priority) for f in result_a.followups],
    )
    await repo.complete_task_with_followups(
        claimed2.id,
        result={"metadata_id": 2},
        followups=[(f.key, f.task_type, f.payload, f.priority) for f in result_b.followups],
    )

    tasks = await _list_actor_tasks(repo)
    assert len(tasks) == 1
    await worker.stop()
