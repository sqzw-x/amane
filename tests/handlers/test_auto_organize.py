"""库级自动整理: 刮削成功后的单文件 ORGANIZE 后继, 与单文件范围在整理侧的行为.

fixture 无法表达的部分: 后继的 key / 范围 / 优先级, 缺文件与缺库时的降级, 以及范围消失不再显示为全零成功.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from amane.config import ActorScrapingConfig, HotSettings, ScrapingConfig
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.models import MediaMetadata
from amane.db.models import MediaFileStatus, TaskType
from amane.enums import SiteName
from amane.handlers import (
    FollowupTask,
    OrganizeHandler,
    OrganizePayload,
    ScrapeHandler,
    ScrapePayload,
    ScrapeResult,
    TaskResult,
)
from amane.handlers._common import register_media_file

if TYPE_CHECKING:
    from amane.db.repository import Repository
    from amane.media import ResourceStore


class _FakeCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://fake.example.com")

    def __init__(self, metadata: MediaMetadata) -> None:
        self._profile = self.profile()
        self.name = self._profile.name
        self._metadata = metadata

    async def _search(self, query: object, options: object = None) -> str | None:
        return "https://fake.example.com/v/1"

    async def _scrape(self, url: str, options: object = None) -> MediaMetadata | None:
        return self._metadata


def _handler(repo: Repository) -> ScrapeHandler:
    metadata = MediaMetadata.model_validate({"number": "MIDV-123", "title": "Test", "studio": "Studio X"})
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {"javdb": _FakeCrawler(metadata)}
    cfg = HotSettings(
        scraping=ScrapingConfig(field_priority={}),
        actor_scraping=ActorScrapingConfig(auto_scrape=False, download_images=False),
    )
    return ScrapeHandler(repo, factory, AsyncMock(), cfg)


async def _library_with_file(repo: Repository, tmp_path: Path, *, auto_organize: bool) -> tuple[int, int]:
    lib_root = tmp_path / "lib"
    lib_root.mkdir(exist_ok=True)
    lib = await repo.create_library(name="t", path=str(lib_root), auto_organize=auto_organize)
    assert lib.id is not None
    media = await register_media_file(repo, lib.id, lib_root / "incoming" / "MIDV-123.mp4")
    assert media.id is not None
    return lib.id, media.id


def _organize_followups(result: TaskResult[ScrapeResult]) -> list[FollowupTask]:
    return [f for f in result.followups if f.task_type == TaskType.ORGANIZE]


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_chains_single_file_scope(repo: Repository, tmp_path: Path) -> None:
    """开关开启时, 为本次刮削的那一个文件描述 ORGANIZE 后继 (范围单文件, priority=-1)."""
    lib_id, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))

    assert result.success is True
    organize = _organize_followups(result)
    assert len(organize) == 1
    assert organize[0].key == f"organize:{media_id}"
    assert organize[0].priority == -1
    assert organize[0].payload["library_id"] == lib_id
    assert organize[0].payload["media_file_ids"] == [media_id]


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_disabled_chains_nothing(repo: Repository, tmp_path: Path) -> None:
    _, media_id = await _library_with_file(repo, tmp_path, auto_organize=False)
    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))

    assert result.success is True
    assert _organize_followups(result) == []


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_ignores_by_number_scrape(repo: Repository, tmp_path: Path) -> None:
    """按番号刮削没有可落盘的文件, 库未知, 因此不链整理."""
    await _library_with_file(repo, tmp_path, auto_organize=True)
    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=None))

    assert result.success is True
    assert _organize_followups(result) == []


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_skips_missing_media_file(repo: Repository, tmp_path: Path) -> None:
    """索引与任务不一致 (行已消失) 时不链, 也不阻断刮削."""
    _, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    await repo.delete_media_file(media_id)

    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))
    assert result.success is True
    assert _organize_followups(result) == []


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_skips_missing_library(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)

    async def _gone(_library_id: int):
        return None

    monkeypatch.setattr(repo, "get_library", _gone)
    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))
    assert result.success is True
    assert _organize_followups(result) == []


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_followup_lands_in_task_graph(repo: Repository, tmp_path: Path) -> None:
    """后继经完成事务落库: 整理任务成为刮削任务的子节点, 链根是那次刮削."""
    _, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    result = await _handler(repo).handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))
    followups = [(f.key, f.task_type, f.payload, f.priority) for f in _organize_followups(result)]
    assert len(followups) == 1

    scrape = await repo.create_task(TaskType.SCRAPE, payload={"number": "MIDV-123"})
    assert scrape.id is not None
    claimed = await repo.claim_next_task()
    assert claimed is not None
    assert claimed.id is not None
    await repo.complete_task_with_followups(claimed.id, result=result.as_dict(), followups=followups)

    organize_tasks = await repo.list_tasks(task_types=[TaskType.ORGANIZE])
    assert len(organize_tasks) == 1
    assert organize_tasks[0].root_task_id == scrape.id
    assert organize_tasks[0].payload["media_file_ids"] == [media_id]


@pytest.mark.asyncio(loop_scope="function")
async def test_auto_organize_chains_again_on_rescrape(repo: Repository, tmp_path: Path) -> None:
    """重新刮削再链一条 (ORGANIZE 不复用已有行); 已就位的重复整理由整理侧幂等兜底."""
    _, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    handler = _handler(repo)

    first = await handler.handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))
    second = await handler.handle(ScrapePayload(number="MIDV-123", media_file_id=media_id))
    assert len(_organize_followups(first)) == 1
    assert len(_organize_followups(second)) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_single_file_scope_resolves_without_path_conflict(repo: Repository, tmp_path: Path) -> None:
    """单文件范围经 resolve 后 path 是库根, 不触发 path 与 media_file_ids 互斥的 422."""
    lib_id, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    payload = OrganizePayload(library_id=lib_id, media_file_ids=[media_id])
    await payload.resolve(repo)
    assert payload.media_file_ids == [media_id]


@pytest.mark.asyncio(loop_scope="function")
async def test_missing_scope_is_not_all_zero_success(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """范围非空但一行都取不到时记 skipped, 不是「整理成功且什么都没做」."""
    lib_id, media_id = await _library_with_file(repo, tmp_path, auto_organize=True)
    await repo.delete_media_file(media_id)

    result = await OrganizeHandler(repo, HotSettings(), resource_store).handle(
        OrganizePayload(library_id=lib_id, media_file_ids=[media_id])
    )
    assert result.success is True
    assert result.result is not None
    assert result.result.skipped == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_scope_stays_empty_run(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """显式空范围是文档化的空跑 (用户没勾选任何行), 仍算成功."""
    lib_id, _ = await _library_with_file(repo, tmp_path, auto_organize=True)

    result = await OrganizeHandler(repo, HotSettings(), resource_store).handle(
        OrganizePayload(library_id=lib_id, media_file_ids=[])
    )
    assert result.success is True
    assert result.result is not None
    assert result.result.skipped == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_repeated_single_file_organize_keeps_file_in_place(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """同一文件整理两次: 第二次走已就位分支, 文件仍在目标路径且不算失败."""
    lib_root = tmp_path / "lib"
    incoming = lib_root / "incoming"
    incoming.mkdir(parents=True)
    src = incoming / "MIDV-123.mp4"
    src.write_bytes(b"video")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, auto_organize=True)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="MIDV-123", studio="Studio")
    assert meta.id is not None
    media = await repo.create_media_file(
        lib.id, path=str(src), number="MIDV-123", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert media.id is not None

    handler = OrganizeHandler(repo, HotSettings(), resource_store)
    payload = OrganizePayload(library_id=lib.id, media_file_ids=[media.id])
    first = await handler.handle(payload)
    second = await handler.handle(OrganizePayload(library_id=lib.id, media_file_ids=[media.id]))

    assert first.success is True and second.success is True
    assert first.result is not None and second.result is not None
    assert first.result.organized == 1
    assert second.result.failed == 0 and second.result.conflicted == 0
    moved = await repo.get_media_file(media.id)
    assert moved is not None
    assert moved.path != str(src)
    assert Path(moved.path).read_bytes() == b"video"
