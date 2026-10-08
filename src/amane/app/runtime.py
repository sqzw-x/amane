"""经 ``app.state.runtime`` / ``RuntimeDep`` 注入. ``apply_rebuild()`` 在 HotSettings 变更时重建依赖热配置的对象."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
import structlog

from ..config import R18Config
from ..crawlers import actor_registry, registry
from ..crawlers.base import CrawlerProfile
from ..crawlers.factory import CrawlerFactory
from ..crawlers.http import HttpClient
from ..crawlers.r18dev import R18Database
from ..db.models import TaskType
from ..enums import BrowserBackendName, BrowserMode, SiteName
from ..handlers import (
    ActorScrapeHandler,
    CleanupHandler,
    DeleteHandler,
    LibraryTaskLocks,
    OrganizeHandler,
    R18ImportHandler,
    RefreshHandler,
    RescrapeHandler,
    ScanInvalidHandler,
    ScrapeHandler,
    UpscaleHandler,
)
from ..library import InventoryStore
from ..llm import TranslationCache, build_translator
from ..media.watermarks import user_watermark_dir
from ..net.browser import BrowserPool
from ..net.http import RateLimiters, WebClient
from ..playback import PlaybackFactory, PlaybackState
from ..plugins.manager import PluginManager
from ..plugins.packaging import install_plugin_path, install_plugin_zip, uninstall_plugin_tree
from ..scheduler.worker import MAIN_LOOP_STOP_TIMEOUT, AsyncWorker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..agent import AgentService
    from ..config import BrowserConfig, ConfigManager, HotSettings
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
    browser: BrowserPool


def build_network_stack(
    hot: HotSettings,
    r18_db: R18Database | None = None,
    *,
    data_dir: Path | None = None,
    plugin_manager: PluginManager | None = None,
    browser: BrowserPool | None = None,
) -> NetworkStack:
    """bootstrap 与热重载共用. r18_db 为会话级只读引擎, 热重载时复用同一实例, 不随配置重建.

    ``browser`` 由热重载传入未变化的浏览器池以保留已解决的会话; 复用时就地同步新的 HTTP 通道
    (solver 经它出站). 不传则按当前配置构造.
    """
    site_urls: dict[str, list[str]] = {}
    referer_hosts: set[str] = set()
    site_config = hot.scraping.site_config

    def _register_site(site: str, profile: CrawlerProfile) -> None:
        urls = [*profile.urls, profile.base_url]
        site_urls[site] = urls
        if not profile.same_origin_referer:
            return
        # 用户配置的镜像域同样纳入, 否则图片仍按裸请求发出.
        configured = site_config.get(site)
        for raw in (configured.base_url if configured else None, *urls):
            host = httpx.URL(raw).host if raw else None
            if host is not None:
                referer_hosts.add(host)

    for site in registry.sites():
        crawler_cls = registry.get(site)
        if crawler_cls:
            _register_site(site, crawler_cls.profile())
    for name in actor_registry.sites():
        crawler_cls = actor_registry.get(name)
        if crawler_cls:
            _register_site(str(SiteName(name)), crawler_cls.profile())

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
        same_origin_referer_hosts=frozenset(referer_hosts),
    )
    if browser is None:
        browser = BrowserPool(
            default_backend=hot.network.browser.backend,
            solver_url=hot.network.browser.solver_url,
            proxy=hot.network.proxy,
            web_client=web_client,
            timeout_ms=hot.network.browser.timeout,
        )
    else:
        browser.rebind_web_client(web_client)
    _warn_disabled_browser_sources(hot, browser)
    http_client = HttpClient(web=web_client, browser=browser, browser_timeout=hot.network.browser.timeout)
    factory = CrawlerFactory(
        http_client,
        site_configs=hot.scraping.site_config,
        r18_db=r18_db,
        data_dir=data_dir,
        gfriends_repo=hot.actor_scraping.gfriends_repo,
        plugin_manager=plugin_manager,
        plugin_configs=hot.plugins,
    )

    return NetworkStack(web_client=web_client, http_client=http_client, factory=factory, browser=browser)


def _warn_disabled_browser_sources(hot: HotSettings, browser: BrowserPool) -> None:
    """一律使用浏览器却没有可用后端的来源: 获取时必然失败, 在构造期给出一次明确告警.

    ``auto`` 在无后端时退化为直连, ``browser_backend=off`` 是显式禁用, 均不在此告警.
    """
    for site, config in hot.scraping.site_config.items():
        if config.use_browser is not BrowserMode.ALWAYS or config.browser_backend is BrowserBackendName.OFF:
            continue
        if browser.resolve(config.browser_backend) is None:
            logger.warning("browser rendering enabled without backend", site=str(site))


def build_r18_db(r18: R18Config) -> R18Database | None:
    """从 r18 配置构建只读引擎. dsn 未配置或建连失败时返回 None (数据源静默禁用).

    引擎构造是同步的 (create_async_engine 仅初始化连接池, 不立即连接), 故可在 _rebuild() 内调用.
    """
    if not r18.enabled:
        return None
    try:
        return R18Database(r18.read_url())
    except Exception:
        structlog.get_logger().warning("r18 read engine not created", exc_info=True)
        return None


@dataclass(eq=False)
class R18Handle:
    """r18 引擎的所有权标记. 引擎对象本身仍以 ``R18Database`` 注入 factory / crawler."""

    engine: R18Database
    closed: bool = False


@dataclass(eq=False)
class RetiringWorker:
    """已退役、正在排空的 worker 及其使用的 r18 句柄与浏览器池."""

    worker: AsyncWorker
    r18: R18Handle | None
    browser: BrowserPool | None


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
    playback_factory: PlaybackFactory | None = None
    playback_state: PlaybackState = field(default_factory=PlaybackState)
    library_locks: LibraryTaskLocks = field(default_factory=LibraryTaskLocks)
    inventory_store: InventoryStore = field(default_factory=InventoryStore)
    browser: BrowserPool | None = None
    r18_handle: R18Handle | None = None

    _r18_config: R18Config | None = field(default=None, repr=False)
    _browser_key: tuple[BrowserConfig, str | None] | None = field(default=None, repr=False)
    _rebuild_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _retiring: list[RetiringWorker] = field(default_factory=list, repr=False)
    _retire_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    _closing: bool = field(default=False, repr=False)
    _stopped: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self._r18_config is None:
            self._r18_config = self.config.hot.r18.model_copy(deep=True)
        if self.r18_handle is None and self.r18_db is not None:
            self.r18_handle = R18Handle(self.r18_db)
        self._browser_key = self._current_browser_key()

    def _current_browser_key(self) -> tuple[BrowserConfig, str | None]:
        """浏览器池的生命周期键: 任一变化都需要换新引擎 (代理与超时参与启动与单次渲染)."""
        network = self.config.hot.network
        return (network.browser.model_copy(deep=True), network.proxy)

    def _rebuild(self) -> None:
        """重建依赖热配置的对象. 调用方必须持有 ``_rebuild_lock`` (见 ``apply_rebuild``).

        r18 只读引擎随 hot.r18 变更而重建; 引擎与浏览器池的旧实例由退役流程在引用它们的 worker 排空后释放.
        rebuild 是同步的, 不能 await.
        """
        hot = self.config.hot
        paused = self.worker.is_paused

        # 日志级别即时生效, 无需重建
        logging.getLogger("amane").setLevel(hot.logging.level)

        # r18 配置变更时重建只读引擎与新句柄; 旧句柄随退役 worker 释放
        if hot.r18 != self._r18_config:
            self.r18_db = build_r18_db(hot.r18)
            self.r18_handle = R18Handle(self.r18_db) if self.r18_db is not None else None
            self._r18_config = hot.r18.model_copy(deep=True)

        # 浏览器池只在其生命周期键变化时重建; 复用同一实例保留已解决的挑战会话
        browser_key = self._current_browser_key()
        reused_browser = None if browser_key != self._browser_key else self.browser
        self._browser_key = browser_key

        stack = build_network_stack(
            hot,
            r18_db=self.r18_db,
            data_dir=self.config.cold.data_dir,
            plugin_manager=self.plugin_manager,
            browser=reused_browser,
        )
        self.web_client = stack.web_client
        self.http_client = stack.http_client
        self.factory = stack.factory
        self.browser = stack.browser
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
                library_locks=self.library_locks,
                inventory_store=self.inventory_store,
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

        previous_playback = self.playback_factory
        # 新的 Factory 构造时丢弃解析结果缓存 (配置改动可能更换凭据); token 表与探测缓存仍在
        # ``playback_state`` 里, 只有插件集合变化才 reset().
        current_playback = PlaybackFactory(
            plugin_manager=self.plugin_manager,
            plugin_configs=hot.plugins,
            http_client=self.http_client,
            web_client=self.web_client,
            data_dir=self.config.cold.data_dir,
            proxy=hot.network.proxy,
            state=self.playback_state,
        )
        if previous_playback is not None and set(previous_playback.playback_source_ids()) != set(
            current_playback.playback_source_ids()
        ):
            # 插件集合变化 (安装 / 卸载 / 重载 / 启停): 已签发的 token 与探测缓存必须失效.
            self.playback_state.reset()
        self.playback_factory = current_playback

    async def apply_rebuild(self) -> None:
        """串行化 rebuild 与 worker 替换; 旧资源由退役流程在 worker 排空后释放."""
        async with self._rebuild_lock:
            self._ensure_open()
            await self._apply_rebuild_unlocked()

    async def reload_plugins(self) -> PluginManager:
        """Rediscover drop-ins under ``plugins/sources`` and rebuild the scrape stack."""
        async with self._rebuild_lock:
            self._ensure_open()
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            return self._require_plugin_manager()

    async def install_plugin_archive(self, payload: bytes) -> PluginManager:
        """Install a zip of ``plugin.py`` into ``plugins/sources`` and rebuild."""
        async with self._rebuild_lock:
            self._ensure_open()
            plugin_id = install_plugin_zip(self.config.cold.data_dir, payload)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            logger.info("source plugin installed", plugin_id=plugin_id)
            return self._require_plugin_manager()

    async def install_plugin_from_path(self, source: Path) -> PluginManager:
        """Copy a server path (directory or zip) into ``plugins/sources`` and rebuild."""
        async with self._rebuild_lock:
            self._ensure_open()
            plugin_id = install_plugin_path(self.config.cold.data_dir, source)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()
            logger.info("source plugin installed", plugin_id=plugin_id)
            return self._require_plugin_manager()

    async def uninstall_plugin_tree(self, plugin_id: str) -> None:
        """Remove ``plugins/sources/<plugin_id>`` and rediscover sources."""
        async with self._rebuild_lock:
            self._ensure_open()
            manager = self._require_plugin_manager()
            if manager.get(plugin_id) is None:
                raise KeyError(plugin_id)
            uninstall_plugin_tree(self.config.cold.data_dir, plugin_id)
            self._replace_plugin_manager(PluginManager.discover(self.config.cold.data_dir))
            await self._apply_rebuild_unlocked()

    async def _apply_rebuild_unlocked(self) -> None:
        """必须持有 ``_rebuild_lock``."""
        old_playback = self.playback_factory
        old_worker = self.worker
        old_r18 = self.r18_handle
        old_browser = self.browser
        self._rebuild()
        old_worker.retire()
        self.worker.start()
        self._retire_worker(old_worker, old_r18, old_browser)
        if self._retiring:
            logger.warning("workers retiring", count=len(self._retiring))
        if old_playback is not None:
            await old_playback.aclose()

    def _retire_worker(self, worker: AsyncWorker, r18: R18Handle | None, browser: BrowserPool | None) -> None:
        entry = RetiringWorker(worker=worker, r18=r18, browser=browser)
        self._retiring.append(entry)
        task = asyncio.create_task(self._drain_and_close(entry))
        self._retire_tasks.add(task)

    async def _drain_and_close(self, entry: RetiringWorker) -> None:
        """等退役 worker 排空后释放其 r18 句柄与浏览器池; 不获取 ``_rebuild_lock``."""
        try:
            await entry.worker.drain()
            self._retiring.remove(entry)
            if entry.r18 is not None:
                await self._release_r18(entry.r18)
            if entry.browser is not None:
                await self._release_browser(entry.browser)
        except Exception:
            logger.exception("retiring worker cleanup failed")
        finally:
            self._retire_tasks.discard(asyncio.current_task())
        logger.info("worker retired", active_count=entry.worker.active_count)

    async def _release_r18(self, handle: R18Handle) -> None:
        """句柄不再被当前或任何退役 worker 使用时关闭; 判定与置位在同一同步段."""
        if handle.closed or handle is self.r18_handle:
            return
        if any(entry.r18 is handle for entry in self._retiring):
            return
        handle.closed = True
        await handle.engine.close()

    async def _release_browser(self, browser: BrowserPool) -> None:
        """池不再被当前或任何退役 worker 使用时关闭."""
        if browser is self.browser:
            return
        if any(entry.browser is browser for entry in self._retiring):
            return
        await browser.close()

    async def cancel_task(self, task_id: int) -> bool:
        """当前 worker 与退役 worker 都能命中."""
        if await self.worker.cancel_task(task_id):
            return True
        for entry in list(self._retiring):
            if await entry.worker.cancel_task(task_id):
                return True
        return False

    async def stop_workers(self, *, closing: bool = True) -> None:
        """关闭编排: 停全部 worker 的认领, 限时处置活跃任务, 单次清扫. 幂等."""
        async with self._rebuild_lock:
            if self._stopped:
                return
            if closing:
                self._closing = True
            workers = [self.worker, *(entry.worker for entry in self._retiring)]
            self._retire_worker(self.worker, self.r18_handle, self.browser)
            for worker in workers:
                worker.retire()
            for worker in workers:
                try:
                    await asyncio.wait_for(worker.wait_stopped(), timeout=MAIN_LOOP_STOP_TIMEOUT)
                except TimeoutError:
                    logger.warning("worker main loop stuck, cancelling", timeout=MAIN_LOOP_STOP_TIMEOUT)
                    worker.cancel_main_loop()
                    await worker.wait_stopped()
            for worker in workers:
                await worker.shutdown_active()
            failed = await self.repo.fail_all_running_tasks()
            for task in list(self._retire_tasks):
                with suppress(Exception):
                    await task
            if self.r18_handle is not None and not self.r18_handle.closed:
                self.r18_handle.closed = True
                with suppress(Exception):
                    await self.r18_handle.engine.close()
            self._stopped = True
            logger.info("workers stopped", marked_failed=failed)

    def _ensure_open(self) -> None:
        if self._closing:
            raise RuntimeError("运行时正在关闭")

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
    library_locks: LibraryTaskLocks | None = None,
    inventory_store: InventoryStore | None = None,
) -> dict[TaskType, TaskHandler[Any, Any]]:
    # 未启用/缺密钥时 translator 为 None, ScrapeHandler 跳过翻译.
    # 经 _rebuild() 热重载; 代理沿用 network.proxy.
    # 译文缓存是会话级, 热重载时复用同一实例.
    translator = build_translator(
        enabled=hot.llm.enabled,
        api_type=hot.llm.api_type,
        api_key=hot.llm.api_key,
        base_url=hot.llm.base_url,
        model=hot.llm.model,
        rate_limit=hot.llm.rate_limit,
        proxy=hot.network.proxy,
        system_prompt=hot.llm.system_prompt,
        field_prompts=hot.llm.field_prompts,
        cache=translation_cache,
    )
    if library_locks is None:
        library_locks = LibraryTaskLocks()
    # 缺省自建只服务精简构造 (测试); 生产必须传入 AppRuntime 的那一份, 否则扫描写进的清单
    # 不在面板读取的存放里, 删除任务只会得到「清单不存在」.
    if inventory_store is None:
        inventory_store = InventoryStore()
    handlers: dict[TaskType, TaskHandler[Any, Any]] = {
        TaskType.REFRESH: RefreshHandler(repo, hot.watcher.media_extensions, inventory_store),
        TaskType.SCRAPE: ScrapeHandler(
            repo,
            factory,
            resource_store,
            hot,
            web_client,
            translator,
            plugin_manager.descriptors() if plugin_manager is not None else None,
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
        TaskType.SCAN_INVALID: ScanInvalidHandler(repo, hot, inventory_store),
        TaskType.DELETE: DeleteHandler(repo, inventory_store, hot, library_locks=library_locks),
        TaskType.CLEANUP: CleanupHandler(repo=repo, resource_store=resource_store),
        TaskType.UPSCALE: UpscaleHandler(resource_store, hot),
        TaskType.RESCRAPE: RescrapeHandler(repo),
    }
    # state_dir 缺省回退 cwd/data (精简构造场景).
    handlers[TaskType.R18_IMPORT] = R18ImportHandler(
        config=hot.r18, web_client=web_client, state_dir=state_dir if state_dir is not None else Path("./data")
    )
    return handlers
