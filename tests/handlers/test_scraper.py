"""测试 amane.pipeline - ScrapeHandler 和 RefreshHandler"""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from amane.config import HotSettings, ScrapingConfig
from amane.crawlers.models import MediaMetadata
from amane.db.models import MediaFileStatus, TaskType
from amane.enums import MetadataField, SiteName
from amane.handlers import RefreshHandler, RefreshPayload, ScanMode, ScrapeHandler, ScrapePayload
from amane.parsing import ContentType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from amane.db.repository import Repository

# --- 辅助类 ---


class MockCrawler:
    name = SiteName.JAVDB

    def __init__(self, client=None):
        self.client = client

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        return MediaMetadata(
            number="MIDV-123",
            title="Mock Title",
            actors=["Actor A"],
            tags=["Drama"],
            studio="MockStudio",
        )


class EmptySearchCrawler:
    """返回空搜索结果的爬虫"""

    name = SiteName.FC2

    def __init__(self, client=None):
        self.client = client

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        return None


class FakeFactory:
    """伪造的 CrawlerFactory, 返回预配置的爬虫实例"""

    def __init__(self, crawlers: dict):
        self._crawlers = crawlers

    async def get(self, name: str):
        return self._crawlers.get(name)

    async def get_crawlers(self, names: Iterable[str]) -> dict:
        result = {}
        for name in names:
            crawler = await self.get(name)
            if crawler is not None:
                result[name] = crawler
        return result


@pytest.fixture
def factory():
    return FakeFactory({"javdb": MockCrawler()})


@pytest.fixture
def handler(repo, factory, resource_store):
    return ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=HotSettings())


# --- ScrapeHandler 测试 ---


class TestScrapeHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_scrape(self, repo: Repository, handler):
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        # 验证元数据已存储
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "Mock Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reports_determinate_progress(self, repo: Repository, handler):
        """ScrapeHandler 上报 determinate 进度: 字段抓取 → materialize → done."""
        events: list[tuple[int, int, str]] = []

        async def capture(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        handler.set_progress_callback(capture)
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True

        assert events
        total = events[-1][1]
        assert total > 0
        assert events[0] == (0, total, "fetch")
        assert events[-1] == (total, total, "done")
        assert all(t == total for _, t, _ in events)
        assert [c for c, _, _ in events] == sorted(c for c, _, _ in events)
        assert any(m == "materialize" for _, _, m in events)
        # 抓取结束抬到标量满分 (= total - 2, 留给 materialize/persist)
        assert any(c == total - 2 and m == "fetch" for c, _, m in events)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_updates_media_file_status(self, repo: Repository, handler):
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        assert media.id is not None
        await handler.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_payload_fields_raises(self, handler):
        """parse_payload 在字段缺失时抛出异常"""
        with pytest.raises((TypeError, KeyError, ValidationError)):
            handler.parse_payload({"media_file_id": 1})

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_results_marks_failed(self, repo: Repository, resource_store):
        """所有爬虫返回空结果时标记失败"""
        empty_factory = FakeFactory({"javdb": EmptySearchCrawler()})
        h = ScrapeHandler(
            repo=repo,
            factory=empty_factory,
            resource_store=resource_store,
            pipeline_config=HotSettings(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/TEST-001.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="TEST-001", content_type=ContentType.CENSORED)
        )
        assert result.success is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_materializes_cropped_poster(self, repo: Repository, resource_store):
        """有 web_client 时, scrape 物化: poster 候选偏矮 → 裁剪 → metadata 记内部 URL."""
        from typing import ClassVar, cast

        from PIL import Image

        from amane.media.pipeline import RESOURCE_URL_PREFIX
        from amane.net.http import WebClient

        class ImgCrawler:
            name = SiteName.JAVDB

            def __init__(self, client=None):
                pass

            async def fetch(self, query, options=None) -> MediaMetadata | None:
                return MediaMetadata(
                    number="MIDV-123",
                    title="T",
                    thumb_urls=["https://s/t.jpg"],
                    poster_urls=["https://s/p.jpg"],
                )

        class FakeWebClient:
            sizes: ClassVar = {"https://s/t.jpg": (800, 538), "https://s/p.jpg": (300, 420)}

            async def download(self, url, dest, **kwargs) -> bool:
                dest.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", self.sizes[url], "blue").save(dest)
                return True

            async def get_filesize(self, url, **kwargs):
                return None

        h = ScrapeHandler(
            repo=repo,
            factory=FakeFactory({"javdb": ImgCrawler()}),
            resource_store=resource_store,
            pipeline_config=HotSettings(),
            web_client=cast("WebClient", FakeWebClient()),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )
        assert result.success is True
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.poster_urls is not None and len(metadata.poster_urls) == 1
        assert metadata.poster_urls[0].startswith(RESOURCE_URL_PREFIX)  # 内部派生 (裁剪)
        assert metadata.thumb_urls == ["https://s/t.jpg"]  # 外部原样


# --- content_routes 资格真值 + 有序路由 ---


class RecordingFactory:
    """记录被请求的站点名, 返回这些名字对应的 MockCrawler."""

    def __init__(self, available: dict):
        self._available = available
        self.requested: list[str] = []

    async def get_crawlers(self, names):
        self.requested = list(names)
        return {n: self._available[n] for n in names if n in self._available}


def _config_with(routes, field_priority=None):
    scraping = ScrapingConfig(content_routes=routes, field_priority=field_priority or {})
    return HotSettings(scraping=scraping)


class TestContentRoutesFiltering:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_only_routed_sites_requested(self, repo: Repository, resource_store):
        """content_routes 决定 content_type 下哪些站点被请求."""
        available = {
            "javdb": MockCrawler(),
            "dmm": MockCrawler(),
            "javbus": MockCrawler(),
        }
        factory = RecordingFactory(available)
        config = _config_with({ContentType.CENSORED: [SiteName.JAVDB, SiteName.DMM]})
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        assert set(factory.requested) == {SiteName.JAVDB, SiteName.DMM}
        assert SiteName.JAVBUS not in factory.requested

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_route_returns_error(self, repo: Repository, resource_store):
        """content_routes 对应项为空列表时该类型刮削失败."""
        factory = RecordingFactory({"javbus": MockCrawler()})
        config = _config_with({ContentType.CENSORED: [SiteName.JAVBUS], ContentType.FC2: []})
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/FC2-1.mp4")
        result = await h.handle(ScrapePayload(media_file_id=media.id, number="FC2-1", content_type=ContentType.FC2))

        assert result.success is False
        assert result.error is not None
        assert "No eligible crawlers" in result.error

    @pytest.mark.asyncio(loop_scope="function")
    async def test_prefer_outside_route_not_requested(self, repo: Repository, resource_store):
        """prefer 不在 route 内的站不会被 get_crawlers 请求."""
        available = {
            "javdb": MockCrawler(),
            "iqqtv": MockCrawler(),
        }
        factory = RecordingFactory(available)
        config = _config_with(
            {ContentType.CENSORED: [SiteName.JAVDB]}, {MetadataField.TITLE: [SiteName.IQQTV, SiteName.JAVDB]}
        )
        h = ScrapeHandler(repo=repo, factory=factory, resource_store=resource_store, pipeline_config=config)

        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        assert list(factory.requested) == [SiteName.JAVDB]
        assert SiteName.IQQTV not in factory.requested


# --- 翻译集成测试 ---


class TestScrapeTranslation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_applied_to_title(self, repo: Repository, factory, resource_store):
        """配置 translator + field_language 时, 标题被翻译后再持久化."""
        from amane.enums import Language, MetadataField

        class StubTranslator:
            async def translate(self, text, target, field, *, use_cache=True):
                return f"[{target}]{text}"

        config = HotSettings()
        config.scraping.field_language = {MetadataField.TITLE: Language.ZH_CN}
        config.llm.translate_fields = [MetadataField.TITLE]
        h = ScrapeHandler(
            repo=repo,
            factory=factory,
            resource_store=resource_store,
            pipeline_config=config,
            translator=StubTranslator(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        await h.handle(ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED))

        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "[zh_cn]Mock Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_translator_failure_keeps_original(self, repo: Repository, factory, resource_store):
        """translator 抛异常时降级保留原值, 刮削不失败 (机会主义)."""
        from amane.enums import Language, MetadataField

        class BrokenTranslator:
            async def translate(self, text, target, field, *, use_cache=True):
                raise RuntimeError("llm down")

        config = HotSettings()
        config.scraping.field_language = {MetadataField.TITLE: Language.ZH_CN}
        h = ScrapeHandler(
            repo=repo,
            factory=factory,
            resource_store=resource_store,
            pipeline_config=config,
            translator=BrokenTranslator(),
        )
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123.mp4")
        result = await h.handle(
            ScrapePayload(media_file_id=media.id, number="MIDV-123", content_type=ContentType.CENSORED)
        )

        assert result.success is True
        metadata = await repo.get_metadata_by_number("MIDV-123")
        assert metadata is not None
        assert metadata.title == "Mock Title"


# --- RefreshHandler 测试 ---


class TestRefreshHandler:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_and_add_files(self, repo: Repository, tmp_path):
        """扫描目录, 注册新文件"""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "SSIS-456.mkv").write_bytes(b"\x00" * 100)
        (tmp_path / "readme.txt").write_text("not a media file")

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 2
        assert result.result.removed == 0
        assert result.result.scrape == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_handle_files(self, repo: Repository, tmp_path):
        """新文件被注册; PENDING 状态的文件被刮削; SKIP 状态的文件不被刮削."""

        async def new_file(name, db: bool, status: MediaFileStatus = MediaFileStatus.PENDING):
            path = tmp_path / name
            path.write_bytes(b"\x00" * 100)
            if db:
                return await repo.create_media_file(library_id=1, path=str(path), status=status)
            return None

        await new_file("MIDV-123.mp4", db=True, status=MediaFileStatus.PENDING)  # 已注册 PENDING -> 刮削
        await new_file("MIDV-124.mp4", db=False)  # 未注册 -> 注册并刮削 (PENDING)
        await new_file("MIDV-125.mp4", db=True, status=MediaFileStatus.SKIP)  # SKIP -> 不刮削

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path)))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1  # 仅 MIDV-124 是新文件
        assert result.result.removed == 0
        assert result.result.scrape == 2  # MIDV-123 + MIDV-124 (MIDV-125 是 SKIP 不刮削)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_recursive_scan(self, repo: Repository, tmp_path):
        """递归扫描子目录"""
        subdir = tmp_path / "season1"
        subdir.mkdir()
        (subdir / "EP01.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), recursive=True))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 2
        assert result.result.removed == 0
        assert result.result.scrape == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_recursive_scan(self, repo: Repository, tmp_path):
        """非递归扫描不进入子目录"""
        subdir = tmp_path / "season1"
        subdir.mkdir()
        (subdir / "EP01.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), recursive=False))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 0
        assert result.result.scrape == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_custom_patterns(self, repo: Repository, tmp_path):
        """自定义 glob 模式过滤"""
        (tmp_path / "video.mp4").write_bytes(b"\x00" * 100)
        (tmp_path / "video.mkv").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), patterns=["*.mp4"]))

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 0
        assert result.result.scrape == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_blacklisted_files_skipped_and_records_removed(self, repo: Repository, tmp_path):
        """黑名单命中的文件不进扫描; ScanMode.remove 时旧记录被当作失效清除."""
        lib = await repo.create_library(name="t", path=str(tmp_path), blacklist_patterns=["广告", "(?i)ads"])
        assert lib.id is not None
        ad = tmp_path / "新片广告.mp4"
        ad.write_bytes(b"\x00" * 100)
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)
        old = await repo.create_media_file(lib.id, path=str(ad), status=MediaFileStatus.SCRAPED, metadata_id=42)
        assert old.id is not None

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 1
        # 旧记录已被清除; 注意 id 会被 SQLite 复用, 断言用路径而不是 id
        assert await repo.get_media_file_by_path(str(ad)) is None
        assert await repo.get_media_file_by_path(str(tmp_path / "MIDV-123.mp4")) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_undersized_videos_skipped_and_records_removed(self, repo: Repository, tmp_path):
        """低于 min_file_size 的视频不进扫描; nfo 即使很小也不当视频过滤; ScanMode.remove 清旧记录."""
        lib = await repo.create_library(name="t", path=str(tmp_path), min_file_size=50)
        assert lib.id is not None
        ad = tmp_path / "ad.mp4"
        ad.write_bytes(b"tiny")
        (tmp_path / "MIDV-123.mp4").write_bytes(b"x" * 100)
        (tmp_path / "note.nfo").write_bytes(b"nfo")
        old = await repo.create_media_file(lib.id, path=str(ad), status=MediaFileStatus.SCRAPED, metadata_id=42)
        assert old.id is not None

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=lib.id,
                path=str(tmp_path),
                scan={ScanMode.add, ScanMode.remove},
                scrape=set(),
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.removed == 1
        assert await repo.get_media_file_by_path(str(ad)) is None
        assert await repo.get_media_file_by_path(str(tmp_path / "MIDV-123.mp4")) is not None
        assert await repo.get_media_file_by_path(str(tmp_path / "note.nfo")) is None

    @pytest.mark.parametrize(
        ("scan_modes", "scrape_statuses", "expected_scrape_tasks"),
        [
            ({ScanMode.add}, set(), 0),
            ({ScanMode.add}, {MediaFileStatus.PENDING}, 1),
        ],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_scan_mode_controls_scrape(
        self,
        repo: Repository,
        tmp_path,
        scan_modes: set[ScanMode],
        scrape_statuses: set[MediaFileStatus],
        expected_scrape_tasks: int,
    ):
        """scan + scrape 决定是否注册文件、是否派生 SCRAPE; REFRESH 不整理文件."""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(
                library_id=1,
                path=str(tmp_path),
                scan=scan_modes,
                scrape=scrape_statuses,
            )
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.added == 1
        assert result.result.scrape == expected_scrape_tasks

        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == expected_scrape_tasks

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_directory_fails(self, repo: Repository, tmp_path):
        """不存在的目录返回失败"""
        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path / "nonexistent")))

        assert result.success is False
        assert result.error is not None
        assert "not a directory" in result.error.lower()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_submits_scrape_tasks_with_correct_payload(self, repo: Repository, tmp_path):
        """验证派生 SCRAPE followup payload 正确 (use_cache 透传)"""
        (tmp_path / "MIDV-123.mp4").write_bytes(b"\x00" * 100)

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(RefreshPayload(library_id=1, path=str(tmp_path), use_cache=set()))

        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == 1
        assert scrapes[0].payload["number"] == "MIDV-123"
        assert scrapes[0].payload["use_cache"] == []  # 空集透传 = 全部强制刷新
        assert scrapes[0].key == f"scrape:{scrapes[0].payload['media_file_id']}"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_fanout_not_capped_by_list_page_size(self, repo: Repository, tmp_path):
        """list_media_files 默认 50 是 GET /media 分页; REFRESH fan-out 必须全量入队."""
        n = 51
        for i in range(n):
            await repo.create_media_file(
                library_id=1, path=str(tmp_path / f"MIDV-{i:03d}.mp4"), status=MediaFileStatus.PENDING
            )

        handler = RefreshHandler(repo=repo)
        result = await handler.handle(
            RefreshPayload(library_id=1, path=str(tmp_path), scan=set(), scrape={MediaFileStatus.PENDING})
        )

        assert result.success is True
        assert result.result is not None
        assert result.result.scrape == n
        scrapes = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
        assert len(scrapes) == n
