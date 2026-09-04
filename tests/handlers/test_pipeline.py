"""集成测试: 完整流水线 (图片下载 + NFO + 文件整理)"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from amane.config import HotSettings, ScrapingConfig
from amane.crawlers.base import Crawler
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.models import MediaMetadata
from amane.db.models import MediaFileStatus
from amane.enums import SiteName
from amane.handlers import OrganizeHandler, OrganizePayload, ScrapeHandler, ScrapePayload
from amane.organize import MoveMode
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


@pytest.fixture
def fake_metadata():
    return MediaMetadata.model_validate(
        {
            "number": "MIDV-123",
            "title": "Test Title",
            "actors": ["Actor A"],
            "studio": "Studio X",
            "release": "2026-01-15",
            "runtime": 120,
            "thumb_urls": ["https://img.example.com/thumb.jpg"],
            "poster_urls": ["https://img.example.com/p1.jpg", "https://img.example.com/p2.jpg"],
            "extrafanart": ["https://img.example.com/extra1.jpg"],
        }
    )


@pytest.fixture
def fake_factory(fake_metadata):
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {"javdb": FakeCrawler(fake_metadata)}
    return factory


@pytest.mark.asyncio(loop_scope="function")
async def test_full_pipeline_with_post_processing(repo: Repository, fake_factory, resource_store, tmp_path: Path):
    """SCRAPE 写元数据, ORGANIZE 落盘 NFO 并移动文件."""
    src_dir = tmp_path / "incoming"
    src_dir.mkdir()
    src_file = src_dir / "MIDV-123.mp4"
    src_file.write_text("fake video")

    lib = await repo.create_library(
        name="test", path=str(src_dir), video_template=str(tmp_path / "organized" / "{studio}/{number}/{number}.{ext}")
    )
    assert lib.id is not None
    media = await repo.create_media_file(library_id=lib.id, path=str(src_file))

    pipeline_config = HotSettings(scraping=ScrapingConfig(field_priority={}))

    async def _fake_download(url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake image data")
        return True

    mock_web_client = AsyncMock()
    mock_web_client.download = AsyncMock(side_effect=_fake_download)

    scrape = ScrapeHandler(
        repo=repo,
        factory=fake_factory,
        resource_store=resource_store,
        pipeline_config=pipeline_config,
        web_client=mock_web_client,
    )
    result = await scrape.handle(
        ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.result is not None
    assert result.result.metadata_id is not None
    assert src_file.exists()

    org = OrganizeHandler(repo, pipeline_config, resource_store, mock_web_client, safe_dirs=[tmp_path])
    org_result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert org_result.success is True

    target_dir = tmp_path / "organized" / "Studio X" / "MIDV-123"
    organized_file = target_dir / "MIDV-123.mp4"
    assert organized_file.exists()
    nfo_file = target_dir / "MIDV-123.nfo"
    assert nfo_file.exists()
    assert "<title>MIDV-123 Test Title</title>" in nfo_file.read_text()
    assert not src_file.exists()

    assert media.id is not None
    media_updated = await repo.get_media_file(media.id)
    assert media_updated is not None
    assert media_updated.status == MediaFileStatus.SCRAPED


@pytest.mark.asyncio(loop_scope="function")
async def test_pipeline_copy_mode_keeps_source(repo: Repository, fake_factory, resource_store, tmp_path: Path):
    """Library.move_mode=copy 时源文件保留."""
    src_dir = tmp_path / "incoming"
    src_dir.mkdir()
    src_file = src_dir / "MIDV-123.mp4"
    src_file.write_text("fake video")

    lib = await repo.create_library(
        name="test",
        path=str(src_dir),
        move_mode=MoveMode.COPY,
        video_template=str(tmp_path / "organized" / "{studio}/{number}/{number}.{ext}"),
    )
    assert lib.id is not None
    media = await repo.create_media_file(library_id=lib.id, path=str(src_file))

    async def _fake_download(url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake image data")
        return True

    mock_web_client = AsyncMock()
    mock_web_client.download = AsyncMock(side_effect=_fake_download)

    scrape = ScrapeHandler(
        repo=repo,
        factory=fake_factory,
        resource_store=resource_store,
        pipeline_config=HotSettings(scraping=ScrapingConfig(field_priority={})),
        web_client=mock_web_client,
    )
    result = await scrape.handle(
        ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    org = OrganizeHandler(
        repo,
        HotSettings(scraping=ScrapingConfig(field_priority={})),
        resource_store,
        mock_web_client,
        safe_dirs=[tmp_path],
    )
    await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert (tmp_path / "organized" / "Studio X" / "MIDV-123" / "MIDV-123.mp4").exists()
    assert src_file.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_scrape_does_not_move_files(repo: Repository, fake_factory, resource_store, tmp_path: Path):
    """SCRAPE 只写元数据, 不整理库内文件."""
    src_file = tmp_path / "MIDV-123.mp4"
    src_file.write_text("fake video")
    media = await repo.create_media_file(library_id=1, path=str(src_file))

    pipeline_config = HotSettings(scraping=ScrapingConfig(field_priority={}))

    handler = ScrapeHandler(
        repo=repo,
        factory=fake_factory,
        resource_store=resource_store,
        pipeline_config=pipeline_config,
    )

    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
    )

    assert result.success is True
    assert src_file.exists()
    assert result.result is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_dead_poster_url_reordered_before_persist(repo: Repository, fake_factory, resource_store, tmp_path: Path):
    """首位 poster URL 下载失败 → 落库 poster_urls 首位为成功 URL (而非优先级最高的死 URL)."""
    src_file = tmp_path / "MIDV-123.mp4"
    src_file.write_text("fake video")
    media = await repo.create_media_file(library_id=1, path=str(src_file))

    pipeline_config = HotSettings(scraping=ScrapingConfig(field_priority={}))

    async def _fake_download(url: str, dest: Path) -> bool:
        if url == "https://img.example.com/p1.jpg":
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake image data")
        return True

    mock_web_client = AsyncMock()
    mock_web_client.download = AsyncMock(side_effect=_fake_download)

    handler = ScrapeHandler(
        repo=repo,
        factory=fake_factory,
        resource_store=resource_store,
        pipeline_config=pipeline_config,
        web_client=mock_web_client,
    )

    result = await handler.handle(
        ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
    )
    assert result.success is True
    assert result.result is not None

    meta = await repo.get_metadata_by_number("MIDV-123")
    assert meta is not None
    assert meta.poster_urls == ["https://img.example.com/p2.jpg", "https://img.example.com/p1.jpg"]
