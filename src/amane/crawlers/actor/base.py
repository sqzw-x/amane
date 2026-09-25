from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

import structlog

from ...net.connectivity import ConnectivityOutcome, probe_get
from .models import ActorMetadata

if TYPE_CHECKING:
    from ...config import SiteConfig
    from ...enums import SiteName
    from ..base import CrawlerProfile
    from ..http import HttpClient


class ActorFetcher(Protocol):
    async def fetch(self, name: str) -> ActorMetadata | None: ...


class ActorCrawler(ABC):
    """默认 ``_search`` → ``_scrape``. 纯头像源可 override ``fetch()`` 直接查索引."""

    @classmethod
    @abstractmethod
    def profile(cls) -> CrawlerProfile: ...

    def __init__(self, client: HttpClient, config: SiteConfig | None = None):
        self._profile = self.profile()
        self.name: SiteName | str = self._profile.name
        self.client = client
        self.config = config
        self.base_url = self._profile.base_url
        self.cookies = dict(self._profile.cookies)
        self.headers = dict(self._profile.headers)
        self._resolve_config()

    def _resolve_config(self) -> None:
        if self.config is None:
            return
        if self.config.base_url:
            self.base_url = self.config.base_url.rstrip("/")
        for k, v in self.config.cookie.items():
            self.cookies[k] = v

    @property
    def logger(self) -> structlog.stdlib.BoundLogger:
        try:
            return self._logger
        except AttributeError:
            self._logger = structlog.get_logger(f"amane.crawlers.actor.{self.name}")
            return self._logger

    async def fetch(self, name: str) -> ActorMetadata | None:
        # 未命中返回 None. HTTP / 拦截失败冒泡 SourceError, 不允许当作 None.
        url = await self._search(name)
        if not url:
            return None
        return await self._scrape(url)

    async def check_connectivity(self) -> ConnectivityOutcome:
        """连通性自检: 缺省 GET ``base_url``. 覆盖点与影片爬虫相同."""
        return await probe_get(self.client.web_client, self.base_url, cookies=self.cookies, headers=self.headers)

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError
