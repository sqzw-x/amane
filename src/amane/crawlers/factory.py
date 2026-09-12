from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..crawlers.models import FetchOptions, MediaMetadata, SearchQuery
from ..enums import SiteName
from ..plugins.api import FilmSourceProvider, PluginContext
from ..plugins.manager import PluginManager
from ..plugins.models import PluginConfig
from .actor import ActorFetcher, GFriendsActorCrawler, actor_registry
from .base import Crawler
from .registry import registry
from .sites import R18DevCrawler

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..aggregate import CrawlerLike
    from ..config import SiteConfig
    from .actor import ActorCrawler
    from .base import Crawler
    from .http import HttpClient
    from .r18dev import R18Database

logger = structlog.get_logger()


class _PluginProviderAdapter(FilmSourceProvider):
    """Validate plugin fetch results as MediaMetadata; errors bubble to invoke_source."""

    def __init__(self, provider: FilmSourceProvider) -> None:
        self._provider = provider

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        result = await self._provider.fetch(query, options)
        if result is None or isinstance(result, MediaMetadata):
            return result
        return MediaMetadata.model_validate(result)


class CrawlerFactory:
    """延迟创建并缓存爬虫. 全部共用一个 HttpClient.

    SiteConfig 在构造期注入; 热重载时工厂与缓存实例一并重建.
    R18Database 为会话级, 不随热重载重建.
    """

    def __init__(
        self,
        http_client: HttpClient,
        site_configs: Mapping[str | SiteName, SiteConfig] | None = None,
        r18_db: R18Database | None = None,
        *,
        data_dir: Path | None = None,
        gfriends_repo: str | None = None,
        plugin_manager: PluginManager | None = None,
        plugin_configs: dict[str, PluginConfig] | None = None,
    ):
        self._http = http_client
        self._site_configs: dict[str, SiteConfig] = {str(k): v for k, v in (site_configs or {}).items()}
        self._r18_db = r18_db
        self._data_dir = data_dir
        self._gfriends_repo = gfriends_repo
        self._plugin_manager = plugin_manager
        self._plugin_configs = plugin_configs or {}
        self._instances: dict[str, Crawler] = {}
        self._plugin_instances: dict[str, _PluginProviderAdapter] = {}
        self._actor_instances: dict[str, ActorCrawler] = {}

    async def get(self, name: str) -> Crawler | FilmSourceProvider | None:
        if name in self._instances:
            return self._instances[name]

        cls = registry.get(name)
        if cls is None and self._plugin_manager is not None and self._plugin_manager.has_film_plugin(name):
            return await self._get_plugin(name)
        if cls is None:
            logger.error("crawler not registered", name=name)
            return None

        site_config = self._site_configs.get(name)
        # R18DevCrawler 额外注入只读 DB.
        if cls is R18DevCrawler:
            instance: Crawler = R18DevCrawler(client=self._http, config=site_config, db=self._r18_db)
        else:
            instance = cls(client=self._http, config=site_config)
        self._instances[name] = instance
        return instance

    async def _get_plugin(self, name: str) -> FilmSourceProvider | None:
        # 插件禁用返回 None; data_dir 缺失抛 RuntimeError.
        if name in self._plugin_instances:
            return self._plugin_instances[name]
        if self._plugin_manager is None:
            return None
        config = self._plugin_configs.get(name, PluginConfig())
        if not config.enabled:
            logger.info("source plugin disabled", source=name)
            return None
        if self._data_dir is None:
            raise RuntimeError("data_dir is required for source plugins")
        plugin_dir = self._data_dir / "plugins" / name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        provider = self._plugin_manager.build_plugin_provider(
            name,
            context=PluginContext(
                source_id=name,
                http_client=self._http,
                web_client=self._http.web_client,
                data_dir=plugin_dir,
            ),
            config=config,
        )
        adapter = _PluginProviderAdapter(provider)
        self._plugin_instances[name] = adapter
        return adapter

    async def get_crawlers(self, names: Iterable[str]) -> dict[str, CrawlerLike]:
        result: dict[str, CrawlerLike] = {}
        for name in names:
            try:
                crawler = await self.get(name)
            except Exception:
                logger.exception("crawler construction failed", name=name)
                crawler = None
            if crawler is not None:
                result[name] = crawler
        return result

    async def get_actor(self, name: str) -> ActorCrawler | None:
        if name in self._actor_instances:
            return self._actor_instances[name]

        cls = actor_registry.get(name)
        if cls is None:
            logger.error("actor crawler not registered", name=name)
            return None

        site_config = self._site_configs.get(name)
        if cls is GFriendsActorCrawler:
            instance: ActorCrawler = GFriendsActorCrawler(
                client=self._http,
                config=site_config,
                data_dir=self._data_dir,
                repo_url=self._gfriends_repo,
            )
        else:
            instance = cls(client=self._http, config=site_config)
        self._actor_instances[name] = instance
        return instance

    async def get_actor_crawlers(self, names: Iterable[str]) -> dict[str, ActorFetcher]:
        result: dict[str, ActorFetcher] = {}
        for name in names:
            crawler = await self.get_actor(name)
            if crawler is not None:
                result[name] = crawler
        return result

    @property
    def active_crawlers(self) -> dict[str, Crawler]:
        return dict(self._instances)
