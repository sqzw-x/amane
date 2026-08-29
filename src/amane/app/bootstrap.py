"""进程生命周期 - 组装并启停完整运行时 (无 FastAPI).

HTTP 经 ``amane.api.app`` lifespan 挂到
``app.state.runtime``; CLI/测试可直接 ``await start_app()`` / ``await session.aclose()``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..agent import AgentService, ResultCache
from ..config import SAFE_DIRS_ALLOW_ALL, ColdSettings, ConfigManager
from ..config.token import resolve_api_token
from ..db.engine import create_async_engine_from_path
from ..db.repository import Repository
from ..events import EventBus
from ..llm import TranslationCache
from ..media import ResourceStore
from ..observability import setup_logging
from ..plugins.manager import PluginManager
from ..release import ReleaseChecker
from ..scheduler.cron import CronScheduler
from ..scheduler.feeds import FeedService
from ..scheduler.service import WatcherService
from ..scheduler.worker import AsyncWorker
from ..utils.random_logging import run_random_logging
from .proxy_failure_cache import ProxyFailureCache
from .runtime import AppRuntime, build_handlers, build_network_stack, build_r18_db

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger()


def build_safe_dirs(cold: ColdSettings, repo_watch_paths: list[str]) -> list[Path] | None:
    """计算文件浏览器 / 路径模板的可信目录边界.

    优先级: ``ALLOW_ALL`` (无限制, 返回 None) > ``AMANE_SAFE_DIRS`` 路径列表 > 启动时已有 library 路径.
    返回空列表表示已配置但没有可用根 (调用方应拒绝路径), 与 None 不同.
    """
    explicit = cold.safe_dirs
    if explicit == SAFE_DIRS_ALLOW_ALL:
        return None
    if explicit is not None:
        return [d.expanduser().resolve() for d in explicit if d.expanduser().resolve().is_dir()]

    dirs: set[Path] = set()
    for wp_path in repo_watch_paths:
        p = Path(wp_path).resolve()
        if p.is_dir():
            dirs.add(p)
    return list(dirs)


@dataclass
class AppSession:
    """一次进程会话: 对外暴露 ``runtime``, 对内持有启停句柄."""

    runtime: AppRuntime
    _engine: AsyncEngine
    _cron_scheduler: CronScheduler
    _cron_task: asyncio.Task[None]
    _feed_service: FeedService
    _feed_task: asyncio.Task[None]
    _emitter_task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def aclose(self) -> None:
        """停止后台服务并释放连接 / 引擎."""
        logger.info("shutting down amane service")
        await self.runtime.event_bus.close_all()
        if self._emitter_task is not None:
            self._emitter_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._emitter_task
        if self.runtime.watcher_service is not None:
            await self.runtime.watcher_service.stop()
        await self._feed_service.stop()
        self._feed_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._feed_task
        await self._cron_scheduler.stop()
        self._cron_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._cron_task
        await self.runtime.worker.stop()
        await self.runtime.web_client.close()
        if self.runtime.r18_db is not None:
            await self.runtime.r18_db.close()
        if self.runtime.translation_cache is not None:
            await self.runtime.translation_cache.close()
        await self._engine.dispose()
        logger.info("amane service stopped")


async def start_app(config: ConfigManager | None = None) -> AppSession:
    """按架构约定的顺序组装并启动完整进程, 返回可 ``aclose`` 的会话."""
    config = config if config is not None else ConfigManager.with_cold()
    cold = config.cold
    hot = config.hot

    # 0. 事件总线 (先于日志, 以便日志可以转发到 WS)
    event_bus = EventBus()

    # 1. 配置日志
    setup_logging(hot.logging.level, event_bus=event_bus, log_dir=cold.log_dir)

    logger.info("starting amane service", data_dir=str(cold.data_dir))

    plugin_manager = PluginManager.discover(cold.data_dir)
    for failure in plugin_manager.failures:
        logger.warning(
            "source plugin unavailable",
            plugin=failure.name,
            path=failure.value,
            error=failure.error,
        )
    plugin_manager.validate_hot_settings(hot, require_available=False)

    # 2. 数据库 (异步引擎)
    engine = await create_async_engine_from_path(cold.db_path)
    repo = Repository(engine)

    # 2.5 r18.dev 只读引擎 (会话级, 不进 Alembic). 配置在 hot.r18; dsn 变更经 rebuild() 重建.
    r18_db = build_r18_db(hot.r18)
    if r18_db is not None:
        logger.info("r18 read engine ready", db=hot.r18.db_name)

    # 3. 网络层 → 限速器 → HTTP 客户端 → 爬虫工厂
    stack = build_network_stack(
        hot,
        r18_db=r18_db,
        data_dir=cold.data_dir,
        plugin_manager=plugin_manager,
    )
    web_client = stack.web_client
    http_client = stack.http_client
    factory = stack.factory

    # 4. 资源存储层
    resource_store = ResourceStore(engine=engine, base_dir=cold.data_dir / "resources")

    # 4.5 译文缓存 (独立 SQLite, 不进 Alembic; 删除文件即清空). 惰性建连.
    translation_cache = TranslationCache(cold.data_dir / "translations.db")

    # 4.6 助理 Agent (会话级 ResultCache; rebuild 只换工厂不清缓存)
    agent_service = AgentService(
        db_path=cold.db_path,
        data_dir=cold.data_dir,
        repo=repo,
        cache=ResultCache(ttl_s=hot.agent.result_cache_ttl_s, max_entries=hot.agent.result_cache_max_entries),
        config=hot.agent,
    )

    # 5. 信任边界: 安全目录 (路径模板/文件浏览器) + API token (HTTP/WS 鉴权)
    safe_dirs = build_safe_dirs(cold, repo_watch_paths=[lib.path for lib in await repo.list_libraries()])
    if safe_dirs is None:
        logger.info("safe dirs unrestricted", sentinel=SAFE_DIRS_ALLOW_ALL)
    else:
        logger.info("safe dirs for file browser", dirs=[str(d) for d in safe_dirs])
    api_token = resolve_api_token(cold.token, cold.data_dir)
    if api_token is not None:
        logger.info("api token auth enabled", token=api_token)

    # 6. 任务处理器
    handlers = build_handlers(
        repo,
        factory,
        web_client,
        resource_store,
        hot,
        safe_dirs,
        translation_cache,
        cold.data_dir,
        plugin_manager,
    )

    # 7. 启动异步 worker
    worker = AsyncWorker(
        repo=repo,
        handlers=handlers,
        concurrency=hot.worker.concurrency,
        poll_interval=hot.worker.poll_interval,
        shutdown_timeout=hot.worker.shutdown_timeout,
        event_bus=event_bus,
        log_dir=cold.log_dir,
        get_hot=lambda: config.hot,
    )
    worker.start()

    # 7.5 启动 cron 调度器
    cron_scheduler = CronScheduler(repo)
    cron_task = asyncio.create_task(cron_scheduler.start())

    # 7.55 启动 RSS/Atom 发现源
    feed_service = FeedService(repo, web_client)
    feed_task = asyncio.create_task(feed_service.start())

    # 7.6 (dev) 随机日志发射器
    emitter_task: asyncio.Task[None] | None = None
    if cold.test_log:
        emitter_task = asyncio.create_task(run_random_logging())

    # 8. 启动文件监控
    watcher_service = None
    try:
        watcher_service = WatcherService(
            repo=repo,
            event_bus=event_bus,
            use_polling=hot.watcher.use_polling,
            media_extensions=hot.watcher.media_extensions,
            debounce_seconds=hot.watcher.debounce_seconds,
        )
        await watcher_service.start()
    except Exception:
        watcher_service = None
        logger.warning("watcher service not started", exc_info=True)

    runtime = AppRuntime(
        repo=repo,
        config=config,
        worker=worker,
        web_client=web_client,
        http_client=http_client,
        factory=factory,
        event_bus=event_bus,
        release_checker=ReleaseChecker(),
        resource_store=resource_store,
        proxy_failure_cache=ProxyFailureCache(),
        watcher_service=watcher_service,
        feed_service=feed_service,
        safe_dirs=safe_dirs,
        api_token=api_token,
        translation_cache=translation_cache,
        r18_db=r18_db,
        agent_service=agent_service,
        plugin_manager=plugin_manager,
    )
    agent_service.bridge.safe_dirs = None if safe_dirs is None else list(safe_dirs)
    agent_service.bridge.watcher = watcher_service
    agent_service.bridge.cancel_running_task = lambda task_id: runtime.worker.cancel_task(task_id)
    agent_service.bridge.poll_feed = feed_service.poll_one

    logger.info("amane service ready")
    return AppSession(
        runtime=runtime,
        _engine=engine,
        _cron_scheduler=cron_scheduler,
        _cron_task=cron_task,
        _feed_service=feed_service,
        _feed_task=feed_task,
        _emitter_task=emitter_task,
    )
