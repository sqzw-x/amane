"""经 ``app.state.runtime`` / ``RuntimeDep`` 注入. ``rebuild()`` 在 HotSettings 变更时重建依赖热配置的对象."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..config import R18Config
from ..crawlers import actor_registry, registry
from ..crawlers.factory import CrawlerFactory
from ..crawlers.http import HttpClient
from ..crawlers.r18dev import R18Database
from ..db.models import TaskType
from ..enums import SiteName
from ..handlers import (
    ActorScrapeHandler,
    CleanupHandler,
    LibraryTaskLocks,
    OrganizeHandler,
    R18ImportHandler,
    RefreshHandler,
    RescrapeHandler,
    ScrapeHandler,
    TrashHandler,
    UpscaleHandler,
)
from ..llm import TranslationCache, build_translator
from ..media.watermarks import user_watermark_dir
from ..net.http import RateLimiters, WebClient
from ..plugins.manager import PluginManager
from ..plugins.packaging import install_plugin_path, install_plugin_zip, uninstall_plugin_tree
from ..scheduler.worker import AsyncWorker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..agent import AgentService
    from ..config import ConfigManager, HotSettings
    from ..db.repository import Repository
    from ..events import EventBus
    from ..handlers.protocol import TaskHandler
    from ..media import ResourceStore
    from ..release import ReleaseChecker
    from ..scheduler.feeds import FeedService
    from ..scheduler.service import WatcherService
    from .proxy_failure_cache import ProxyFailureCache

logger = structlog.get_logger()


@dataclass
class NetworkStack:
    """limiters → web_client → http_client → factory 的一次构造结果."""

    web_client: WebClient
    http_client: HttpClient
    factory: CrawlerFactory


def build_network_stack(
    hot: HotSettings,
    r18_db: R18Database | None = None,
    *,
    data_dir: Path | None = None,
    plugin_manager: PluginManager | None = None,
) -> NetworkStack:
    """bootstrap 与热重载共用. r18_db 为会话级只读引擎, 热重载时复用同一实例, 不随配置重建."""
    site_urls: dict[str, list[str]] = {}
    for site in registry.sites():
        crawler_cls = registry.get(site)
        if crawler_cls:
            site_urls[str(site)] = [*crawler_cls.profile().urls, crawler_cls.profile().base_url]
    for name in actor_registry.sites():
        crawler_cls = actor_registry.get(name)
        if crawler_cls:
            site = SiteName(name)
            site_urls[str(site)] = [*crawler_cls.profile().urls, crawler_cls.profile().base_url]

    plugin_rates: dict[str, float | None] = {}
    if plugin_manager is not None:
        for descriptor in plugin_manager.descriptors():
            if descriptor.id not in site_urls:
                site_urls[descriptor.id] = list(descriptor.urls)
                plugin_rates[descriptor.id] = descriptor.rate_limit

    limiters = RateLimiters.from_config(
        hot.network.rate_limits,
        hot.scraping.site_config,
        site_urls,
        source_rates=plugin_rates,
        default_rate=hot.network.default_rate_limit,
    )
    web_client = WebClient(
        proxy=hot.network.proxy,
        timeout=hot.network.timeout,
        max_retries=hot.network.max_retries,
        max_clients=hot.network.max_clients,
        limiters=limiters,
    )
    http_client = HttpClient(web=web_client, browser=None)
    factory = CrawlerFactory(
        http_client,
        site_configs=hot.scraping.site_config,
        r18_db=r18_db,
        data_dir=data_dir,
        gfriends_repo=hot.actor_scraping.gfriends_repo,
        plugin_manager=plugin_manager,
        plugin_configs=hot.plugins,
    )

    return NetworkStack(web_client=web_client, http_client=http_client, factory=factory)


def build_r18_db(r18: R18Config) -> R18Database | None:
    """从 r18 配置构建只读引擎. dsn 未配置或建连失败时返回 None (数据源静默禁用).

    引擎构造是同步的 (create_async_engine 仅初始化连接池, 不立即连接), 故可在 rebuild() 内调用.
    """
    if not r18.enabled:
        return None
    try:
        return R18Database(r18.read_url())
    except Exception:
        structlog.get_logger().warning("r18 read engine not created", exc_info=True)
        return None


@dataclass
class AppRuntime:
    repo: Repository
    config: ConfigManager
    worker: AsyncWorker
    web_client: WebClient
    http_client: HttpClient
    factory: CrawlerFactory
    event_bus: EventBus
    release_checker: ReleaseChecker
    resource_store: ResourceStore
    proxy_failure_cache: ProxyFailureCache
    watcher_service: WatcherService | None = None
    feed_service: FeedService | None = None
    safe_dirs: list[Path] | None = field(default_factory=list)
    api_token: str | None = None
    translation_cache: TranslationCache | None = None
    r18_db: R18Database | None = None
    agent_service: AgentService | None = None
    plugin_manager: PluginManager | None = None

    _r18_config: R18Config | None = field(default=None, repr=False)
    _old_r18_db: R18Database | None = field(default=None, repr=False)
    _rebuild_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __post_init__(self) -> None:
        if self._r18_config is None:
            self._r18_config = self.config.hot.r18.model_copy(deep=True)

    def rebuild(self) -> AsyncWorker:
        """重建依赖热配置的对象.

        r18 只读引擎随 hot.r18 变更而重建. rebuild 是同步的, 不能 await;
        旧引擎由 dispose_old_r18 异步释放. 返回旧 worker 以便调用方排空.
        """
        hot = self.config.hot
        old_worker = self.worker
        paused = old_worker.is_paused

        # 日志级别即时生效, 无需重建
        logging.getLogger("amane").setLevel(hot.logging.level)

        # r18 配置变更时重建只读引擎; 旧引擎待异步释放
        if hot.r18 != self._r18_config:
            self._old_r18_db = self.r18_db
            self.r18_db = build_r18_db(hot.r18)
            self._r18_config = hot.r18.model_copy(deep=True)

        stack = build_network_stack(
            hot,
            r18_db=self.r18_db,
            data_dir=self.config.cold.data_dir,
            plugin_manager=self.plugin_manager,
        )
        self.web_client = stack.web_client
        self.http_client = stack.http_client
        self.factory = stack.factory
        if self.feed_service is not None:
            self.feed_service.set_web_client(self.web_client)

        # 使用新处理器与并发数重建 worker
        self.worker = AsyncWorker(
            repo=self.repo,
            handlers=build_handlers(
                self.repo,
                self.factory,
                self.web_client,
                self.resource_store,
                hot,
                self.safe_dirs,
                self.translation_cache,
                self.config.cold.data_dir,
                self.plugin_manager,
            ),
            concurrency=hot.worker.concurrency,
            poll_interval=hot.worker.poll_interval,
            shutdown_timeout=hot.worker.shutdown_timeout,
            event_bus=self.event_bus,
            log_dir=self.config.cold.log_dir,
            get_hot=lambda: self.config.hot,
        )
        self.worker.set_paused(paused)

        if self.agent_service is not None:
            self.agent_service.rebuild(hot.agent)

        return old_worker

    async def apply_rebuild(self) -> None:
        """Serialize rebuild + worker swap + old r18 dispose for config and plugin routes."""
        async with self._rebuild_lock:
            await self._apply_rebuild_unlocked()

    async def reload_plugins(self) -> PluginManager:
        """Rediscover drop-ins under ``plugins/sources`` and rebuild the scrape stack."""
        async with self._rebuild_lock:
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            return self._require_plugin_manager()

    async def install_plugin_archive(self, payload: bytes) -> PluginManager:
        """Install a zip of ``plugin.py`` into ``plugins/sources`` and rebuild."""
        async with self._rebuild_lock:
            plugin_id = install_plugin_zip(self.config.cold.data_dir, payload)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            logger.info("source plugin installed", plugin_id=plugin_id)
            return self._require_plugin_manager()

    async def install_plugin_from_path(self, source: Path) -> PluginManager:
        """Copy a server path (directory or zip) into ``plugins/sources`` and rebuild."""
        async with self._rebuild_lock:
            plugin_id = install_plugin_path(self.config.cold.data_dir, source)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            logger.info("source plugin installed", plugin_id=plugin_id)
            return self._require_plugin_manager()

    async def uninstall_plugin_tree(self, plugin_id: str) -> None:
        """Remove ``plugins/sources/<plugin_id>`` and rediscover sources."""
        async with self._rebuild_lock:
            manager = self._require_plugin_manager()
            if manager.get(plugin_id) is None:
                raise KeyError(plugin_id)
            uninstall_plugin_tree(self.config.cold.data_dir, plugin_id)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()

    async def _apply_rebuild_unlocked(self) -> None:
        """必须持有 ``_rebuild_lock``."""
        old_worker = self.rebuild()
        await old_worker.stop()
        self.worker.start()
        await self.dispose_old_r18()

    def _replace_plugin_manager(self, discovered: PluginManager) -> None:
        discovered.validate_hot_settings(self.config.hot, require_available=False)
        self.plugin_manager = discovered
        for failure in discovered.failures:
            logger.warning(
                "source plugin unavailable",
                plugin=failure.name,
                path=failure.value,
                error=failure.error,
            )

    def _require_plugin_manager(self) -> PluginManager:
        manager = self.plugin_manager
        if manager is None:
            raise RuntimeError("来源插件目录未初始化")
        return manager

    async def dispose_old_r18(self) -> None:
        """异步释放 rebuild() 替换下来的旧 r18 引擎."""
        if self._old_r18_db is not None:
            await self._old_r18_db.close()
            self._old_r18_db = None


def build_handlers(
    repo: Repository,
    factory: CrawlerFactory,
    web_client: WebClient,
    resource_store: ResourceStore,
    hot: HotSettings,
    safe_dirs: Sequence[Path] | None = (),
    translation_cache: TranslationCache | None = None,
    state_dir: Path | None = None,
    plugin_manager: PluginManager | None = None,
) -> dict[TaskType, TaskHandler[Any, Any]]:
    # 未启用/缺密钥时 translator 为 None, ScrapeHandler 跳过翻译.
    # 经 rebuild() 热重载; 代理沿用 network.proxy.
    # 译文缓存是会话级, 热重载时复用同一实例.
    translator = build_translator(
        enabled=hot.llm.enabled,
        api_key=hot.llm.api_key,
        base_url=hot.llm.base_url,
        model=hot.llm.model,
        max_retries=hot.llm.max_retries,
        rate_limit=hot.llm.rate_limit,
        proxy=hot.network.proxy,
        cache=translation_cache,
    )
    library_locks = LibraryTaskLocks()
    handlers: dict[TaskType, TaskHandler[Any, Any]] = {
        TaskType.REFRESH: RefreshHandler(repo, media_extensions=hot.watcher.media_extensions),
        TaskType.SCRAPE: ScrapeHandler(
            repo,
            factory,
            resource_store,
            hot,
            web_client,
            translator,
            plugin_manager.multi_language_sources if plugin_manager is not None else None,
        ),
        TaskType.ACTOR_SCRAPE: ActorScrapeHandler(repo, factory, resource_store, hot, web_client),
        TaskType.ORGANIZE: OrganizeHandler(
            repo,
            hot,
            resource_store,
            web_client,
            safe_dirs,
            watermark_dir=user_watermark_dir(state_dir) if state_dir is not None else None,
            library_locks=library_locks,
        ),
        TaskType.TRASH: TrashHandler(repo, hot, library_locks=library_locks),
        TaskType.CLEANUP: CleanupHandler(repo=repo, resource_store=resource_store),
        TaskType.UPSCALE: UpscaleHandler(resource_store, hot),
        TaskType.RESCRAPE: RescrapeHandler(repo),
    }
    # state_dir 缺省回退 cwd/data (精简构造场景).
    handlers[TaskType.R18_IMPORT] = R18ImportHandler(
        config=hot.r18, web_client=web_client, state_dir=state_dir if state_dir is not None else Path("./data")
    )
    return handlers
