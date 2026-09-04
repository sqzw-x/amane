"""
集成测试: 从文件发现到元数据持久化的完整流水线.

测试流程: 目录扫描 → SCAN 任务 → 注册文件 + 解析番号 → SCRAPE 任务
→ 爬虫 (mock) → 聚合 → DB 存储元数据.

所有文件系统和网络 IO 均已 mock (除 tmp_path 的真实文件).
"""

import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from helpers import AsyncTaskRunner
from pydantic import ValidationError

from amane.config import HotSettings, ScrapingConfig
from amane.crawlers.base import Crawler
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.models import MediaMetadata
from amane.db.models import MediaFileStatus, TaskStatus, TaskType
from amane.enums import SiteName
from amane.handlers import ActorScrapePayload, RefreshHandler, RefreshPayload, ScrapeHandler, ScrapePayload
from amane.handlers.protocol import TaskHandler, TaskResult
from amane.parsing import ContentType
from amane.scheduler.watcher import DEBOUNCE_SECONDS, FileWatcher

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository

# --- 测试夹具 ---


@pytest.fixture
def runner(repo):
    """基于内存仓库的异步任务执行器"""
    return AsyncTaskRunner(repo)


class FakeCrawler(Crawler):
    """无网络 IO, 返回预定义元数据的假爬虫"""

    @classmethod
    def profile(cls):
        from amane.crawlers.base import CrawlerProfile

        return CrawlerProfile(name=SiteName.JAVDB, base_url="https://fake.example.com")

    def __init__(self, metadata: MediaMetadata):
        # 跳过父类 __init__ - 不需要真实 HttpClient
        self._profile = self.profile()
        self.name = self._profile.name
        self._metadata = metadata

    async def _search(self, query, options=None) -> str | None:
        number = query.number if hasattr(query, "number") else query
        return f"https://fake.example.com/v/{number}"

    async def _scrape(self, url: str, options=None) -> MediaMetadata | None:
        return self._metadata


class FailingCrawler(Crawler):
    """总是失败的爬虫 (模拟网络错误)"""

    @classmethod
    def profile(cls):
        from amane.crawlers.base import CrawlerProfile

        return CrawlerProfile(name=SiteName.AVSOX, base_url="https://fail.example.com")

    def __init__(self):
        self._profile = self.profile()
        self.name = self._profile.name

    async def _search(self, query, options=None) -> str | None:
        raise ConnectionError("Network unreachable")

    async def _scrape(self, url: str, options=None) -> MediaMetadata | None:
        raise ConnectionError("Network unreachable")


class HashCrawler(Crawler):
    """声明 uses_file_hash, 记录本次 SearchQuery.file_hash."""

    @classmethod
    def profile(cls):
        from amane.crawlers.base import CrawlerProfile

        return CrawlerProfile(name=SiteName.THEPORNDB, base_url="https://fake.example.com", uses_file_hash=True)

    def __init__(self, metadata: MediaMetadata):
        self._profile = self.profile()
        self.name = self._profile.name
        self._metadata = metadata
        self.seen_hash: str | None = None

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        self.seen_hash = query.file_hash
        return self._metadata

    async def _search(self, query, options=None) -> str | None:
        return "https://fake.example.com/v"

    async def _scrape(self, url: str, options=None) -> MediaMetadata | None:
        return self._metadata


class PassActorHandler(TaskHandler[ActorScrapePayload, dict]):
    """无操作演员处理器: 端到端测试只需消费链式入队的 ACTOR_SCRAPE 任务."""

    def __init__(self):
        super().__init__(payload_t=ActorScrapePayload, result_t=dict)

    async def handle(self, payload: ActorScrapePayload) -> TaskResult[dict]:
        return TaskResult(success=True, result=None)


@pytest.fixture
def fake_metadata():
    """由 FakeCrawler 返回的预构建元数据"""
    return MediaMetadata.model_validate(
        {
            "number": "MIDV-123",
            "title": "Test Title",
            "actors": ["Actor A", "Actor B"],
            "studio": "Test Studio",
            "release": "2026-01-15",
            "runtime": 120,
            "tags": ["tag1", "tag2"],
            "poster_urls": ["https://img.example.com/poster.jpg"],
            "thumb_urls": ["https://img.example.com/thumb.jpg"],
            "score": 8.5,
        }
    )


@pytest.fixture
def fake_factory(fake_metadata):
    """无需真实 HTTP 即可返回 FakeCrawler 的 CrawlerFactory"""
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {
        "javdb": FakeCrawler(fake_metadata),
    }
    return factory


@pytest.fixture
def handler(repo, fake_factory, resource_store):
    return ScrapeHandler(repo=repo, factory=fake_factory, resource_store=resource_store, pipeline_config=HotSettings())


@pytest.fixture
def fail_handler(repo, resource_store):
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {
        "javdb": FailingCrawler(),
    }
    return ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=HotSettings())


@pytest.fixture
def empty_handler(repo, resource_store):
    factory = AsyncMock(spec=CrawlerFactory)
    factory.get_crawlers.return_value = {}  # 空 - 无爬虫
    return ScrapeHandler(repo, factory, resource_store, pipeline_config=HotSettings())


# --- 测试: 文件检测 ---


class TestFileDetection:
    """Watcher 检测新媒体文件并触发回调"""

    def test_new_media_file_triggers_callback(self, tmp_path: Path):
        """新 .mp4 文件在 debounce 后被检测到并传递给回调"""
        received = []
        watcher = FileWatcher(on_file_found=lambda p, _lib: received.append(p))
        watcher.watch(str(tmp_path), library_id=1)

        # 模拟: handler 拾取文件并通过 debounce
        handler = watcher._handlers[0]
        video_path = tmp_path / "MIDV-123.mp4"
        handler._pending[str(video_path)] = time.time() - DEBOUNCE_SECONDS - 1

        ready = watcher.check_debounced()
        assert len(ready) == 1
        assert ready[0] == video_path
        assert received == [video_path]

    def test_non_media_file_not_detected(self, tmp_path: Path):
        """非媒体扩展名被过滤"""
        received = []
        watcher = FileWatcher(on_file_found=lambda p, _lib: received.append(p))
        watcher.watch(str(tmp_path), library_id=1)

        handler = watcher._handlers[0]
        # 模拟 .txt 文件事件 - _handle 会过滤它
        handler._handle(str(tmp_path / "readme.txt"))
        assert handler._pending == {}


# --- 测试: REFRESH 任务 ---


class TestRefreshHandler:
    """RefreshHandler 扫描目录并注册文件"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_refresh_and_submits_scrape(self, repo: Repository, tmp_path: Path):
        """REFRESH 任务扫描目录, 注册文件并创建 SCRAPE 任务"""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 0
        assert result.result.scrape == 1

        # 派生 SCRAPE 作为 followup 描述, 由完成事务创建任务
        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == 1
        assert scrapes[0].payload["number"] == "MIDV-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_refresh_does_not_compute_oshash(self, repo: Repository, tmp_path: Path):
        """REFRESH 只注册路径, 不读文件内容算指纹."""
        video = tmp_path / "MIDV-456.mkv"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))

        result = await RefreshHandler(repo).handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        media = await repo.get_media_file_by_path(str(video))
        assert media is not None
        assert media.oshash is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_refresh_does_not_backfill_oshash(self, repo: Repository, tmp_path: Path):
        """存量缺失指纹的条目扫描时也不回填."""
        video = tmp_path / "OLD-001.mkv"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))
        await repo.create_media_file(library_id=1, path=str(video))

        result = await RefreshHandler(repo).handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        media = await repo.get_media_file_by_path(str(video))
        assert media is not None
        assert media.oshash is None


# --- 测试: SCRAPE 任务 ---


class TestScrapeHandler:
    """ScrapeHandler 从爬虫获取元数据并存入 DB"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_aggregates_and_stores_metadata(self, repo: Repository, handler):
        """SCRAPE 任务调用爬虫并持久化聚合后的元数据"""
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")

        result = await handler.handle(
            ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.metadata_id is not None

        # 验证 DB 中的元数据
        meta = await repo.get_metadata(result.result.metadata_id)
        assert meta is not None
        assert meta.number == "MIDV-123"
        assert meta.title == "Test Title"
        assert meta.actors == ["Actor A", "Actor B"]
        assert meta.studio == "Test Studio"
        assert meta.score == 8.5

        # 验证媒体文件已关联元数据
        assert media.id is not None
        media_updated = await repo.get_media_file(media.id)
        assert media_updated is not None
        assert media_updated.status == MediaFileStatus.SCRAPED
        assert media_updated.metadata_id == meta.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_fails_without_number(self, handler):
        """缺少 number 时 parse_payload 抛出异常"""
        with pytest.raises((TypeError, KeyError, ValidationError)):
            handler.parse_payload({"media_file_id": 1})

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_handles_crawler_failure(self, fail_handler):
        """SCRAPE 任务优雅处理爬虫的网络错误"""
        result = await fail_handler.handle(ScrapePayload(number="MIDV-999", content_type=ContentType.CENSORED))

        # 应失败因为未获取到数据
        assert result.success is False
        assert result.error is not None
        assert "no metadata" in result.error.lower()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_no_crawlers_available(self, empty_handler):
        """无可用爬虫时 SCRAPE 任务失败"""
        result = await empty_handler.handle(ScrapePayload(number="MIDV-000", content_type=ContentType.CENSORED))

        assert result.success is False
        assert result.error is not None
        assert "no crawlers" in result.error.lower()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_computes_oshash_for_stash_crawler(
        self, repo: Repository, resource_store, fake_metadata, tmp_path: Path
    ):
        """路由里有 uses_file_hash 的站时, 刮削前计算并落库指纹."""
        video = tmp_path / "MIDV-123.mkv"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))
        media = await repo.create_media_file(library_id=1, path=str(video))
        crawler = HashCrawler(fake_metadata)
        factory = AsyncMock(spec=CrawlerFactory)
        factory.get_crawlers.return_value = {"theporndb": crawler}
        handler = ScrapeHandler(
            repo,
            factory,
            resource_store,
            pipeline_config=HotSettings(
                scraping=ScrapingConfig(content_routes={ContentType.CENSORED: [SiteName.THEPORNDB]})
            ),
        )
        result = await handler.handle(
            ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
        )
        assert result.success is True
        assert crawler.seen_hash == "a0601fdf9f610000"
        assert media.id is not None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash == "a0601fdf9f610000"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_skips_oshash_without_stash_crawler(self, repo: Repository, handler, tmp_path: Path):
        """默认 javdb 路由不算指纹."""
        video = tmp_path / "MIDV-123.mp4"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))
        media = await repo.create_media_file(library_id=1, path=str(video))
        result = await handler.handle(
            ScrapePayload(number="MIDV-123", media_file_id=media.id, content_type=ContentType.CENSORED)
        )
        assert result.success is True
        assert media.id is not None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash is None


# --- 测试: 端到端流水线 ---


class TestEndToEndPipeline:
    """完整流水线: 目录扫描 → SCAN → SCRAPE → 元数据入库"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_full_pipeline(self, repo: Repository, runner: AsyncTaskRunner, handler, tmp_path: Path):
        """模拟新媒体文件的完整生命周期: 扫描 → 刮削"""
        # 1. 注册处理器
        scan_handler = RefreshHandler(repo)
        scrape_handler = handler
        runner.register_handler(TaskType.REFRESH, scan_handler)
        runner.register_handler(TaskType.SCRAPE, scrape_handler)

        # 2. 创建真实文件
        video = tmp_path / "MIDV-123.mp4"
        video.write_bytes(b"\x00" * 1024)

        # 3. 提交扫描任务
        await repo.create_task(task_type=TaskType.REFRESH, payload=RefreshPayload(library_id=1, path=str(tmp_path)))

        # 4. 处理 SCAN 任务 (扫描目录 → 注册文件 → 提交 SCRAPE)
        processed = await runner.process_one()
        assert processed is True

        # 验证 SCAN 完成
        tasks = await repo.list_tasks()
        scan_tasks = [t for t in tasks if t.type == TaskType.REFRESH]
        assert scan_tasks[0].status == TaskStatus.DONE

        # 5. 处理 SCRAPE 任务
        processed = await runner.process_one()
        assert processed is True

        # 6. 验证最终状态
        media = await repo.get_media_file_by_path(str(video))
        assert media is not None
        assert media.status == MediaFileStatus.SCRAPED
        assert media.metadata_id is not None

        meta = await repo.get_metadata(media.metadata_id)
        assert meta is not None
        assert meta.number == "MIDV-123"
        assert meta.title == "Test Title"

        # 6. 链式: SCRAPE 成功后自动为影片演员入队 ACTOR_SCRAPE 任务
        tasks = await repo.list_tasks()
        actor_tasks = [t for t in tasks if t.type == TaskType.ACTOR_SCRAPE]
        assert len(actor_tasks) == 2  # Actor A / Actor B
        assert all(t.status == TaskStatus.QUEUED for t in actor_tasks)

        # 7. 处理链式演员任务 (生产中由 build_handlers 注册; 此处消费即过)
        runner.register_handler(TaskType.ACTOR_SCRAPE, PassActorHandler())
        assert await runner.process_one() is True
        assert await runner.process_one() is True

        # 8. 队列中无更多任务
        assert await runner.process_one() is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_full_pipeline_with_failing_crawler(
        self, repo: Repository, runner: AsyncTaskRunner, fail_handler, tmp_path: Path
    ):
        """流水线处理爬虫失败 - SCRAPE 失败但系统保持一致"""
        scan_handler = RefreshHandler(repo)
        runner.register_handler(TaskType.REFRESH, scan_handler)
        runner.register_handler(TaskType.SCRAPE, fail_handler)

        # 创建文件并提交扫描
        video = tmp_path / "MIDV-999.mp4"
        video.write_bytes(b"\x00" * 1024)
        await repo.create_task(task_type=TaskType.REFRESH, payload=RefreshPayload(library_id=1, path=str(tmp_path)))

        # 处理 SCAN → 成功
        await runner.process_one()

        # 处理 SCRAPE → 失败 (爬虫错误)
        await runner.process_one()

        # 验证 SCRAPE 任务失败
        tasks = await repo.list_tasks()
        scrape_tasks = [t for t in tasks if t.type == TaskType.SCRAPE]
        assert len(scrape_tasks) == 1
        assert scrape_tasks[0].status == TaskStatus.FAILED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rescan_skips_existing(self, repo: Repository, runner: AsyncTaskRunner, handler, tmp_path: Path):
        """二次扫描同一目录 - 已注册且已刮削的文件不会再被处理"""
        scan_handler = RefreshHandler(repo)
        runner.register_handler(TaskType.REFRESH, scan_handler)
        runner.register_handler(TaskType.SCRAPE, handler)

        video = tmp_path / "MIDV-123.mp4"
        video.write_bytes(b"\x00" * 1024)

        # 第一次扫描
        await repo.create_task(task_type=TaskType.REFRESH, payload=RefreshPayload(library_id=1, path=str(tmp_path)))
        await runner.process_one()  # REFRESH
        await runner.process_one()  # SCRAPE

        # 第二次扫描 - 文件已存在且已刮削, 不会重复添加或派生刮削任务
        await repo.create_task(task_type=TaskType.REFRESH, payload=RefreshPayload(library_id=1, path=str(tmp_path)))
        await runner.process_one()  # REFRESH

        # 验证第二次 REFRESH 的 result: 无新增, 无刮削派生
        tasks = await repo.list_tasks(task_types=[TaskType.REFRESH])
        second_scan = next(t for t in tasks if t.status == TaskStatus.DONE)
        assert second_scan.result is not None
        assert second_scan.result["added"] == 0
        assert second_scan.result["removed"] == 0
        assert second_scan.result["scrape"] == 0
