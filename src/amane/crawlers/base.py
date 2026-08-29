import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from amane.enums import ActorGender
from amane.plugins.models import SourceCapability

from .http import HttpClient

if TYPE_CHECKING:
    from ..config import SiteConfig
    from ..enums import SiteName
    from .models import FetchOptions, MediaMetadata, SearchQuery


@dataclass
class RequestContext:
    """合并爬虫默认值与用户配置后的最终请求配置."""

    base_url: str
    """最终使用的 base URL (末尾无斜杠)."""
    cookies: dict[str, str]
    """合并后的 cookies (类默认 + 用户配置)."""


@dataclass
class CrawlerProfile:
    """爬虫配置概要, 用于构建限速器等组件."""

    name: SiteName | str
    base_url: str
    """站点的默认基础 URL (末尾不带斜杠)."""
    urls: list[str] = field(default_factory=list)
    """该爬虫可能访问的需限速的 URL 列表, 用于限速器匹配."""
    cookies: dict[str, str] = field(default_factory=dict)
    """爬虫默认 cookies."""
    headers: dict[str, str] = field(default_factory=dict)
    """爬虫默认请求头, 如 Accept-Language 用于绕过地域限制."""
    capabilities: frozenset[SourceCapability] = field(default_factory=frozenset)
    """来源能力. 空则影片爬虫视为 film_metadata; 演员爬虫必须显式声明 profile / image."""
    multi_language: bool = False
    """是否消费 FetchOptions.language (聚合展开 (site, lang) 节点)."""
    genders: frozenset[ActorGender] | None = None
    """演员爬虫的性别覆盖; 影片爬虫为 None."""

    def effective_capabilities(self) -> frozenset[SourceCapability]:
        return self.capabilities or frozenset({SourceCapability.FILM_METADATA})


class Crawler(ABC):
    """
    所有爬虫的基类.

    公开接口只有一个方法: fetch(query, options) -> MediaMetadata | None

    默认实现是 Template Method: _search() -> _scrape().
    大部分爬虫只需实现 _search 和 _scrape.
    特殊爬虫 (如 theporndb, official) 可直接 override fetch().

    爬虫是无状态的: SiteConfig 通过方法参数注入而非构造函数.
    FetchOptions 用于传递按次可变的选项 (如语言).

    日志:
        self.logger 提供 per-crawler 命名空间的 logger (amane.crawlers.{name}).
        在 structlog pipeline 下, task_id 等上下文字段自动注入.
        子类可直接使用 self.logger.debug/info/warning/error.
    """

    @classmethod
    @abstractmethod
    def profile(cls) -> CrawlerProfile:
        """返回该爬虫的配置概要."""
        ...

    def __init__(self, client: HttpClient, config: SiteConfig | None = None):
        self._profile = self.profile()
        self.name = self._profile.name
        self.client = client
        self.config = config

        self.base_url = self._profile.base_url
        self.cookies = dict(self._profile.cookies)
        self.headers = dict(self._profile.headers)
        self._resolve_config()

    def _resolve_config(self):
        """合并 CrawlerProfile 与 SiteConfig, 设置相应属性."""
        if self.config is None:
            return

        if self.config.base_url:
            self.base_url = self.config.base_url.rstrip("/")

        for k, v in self.config.cookie.items():
            self.cookies[k] = v

    @property
    def logger(self) -> structlog.stdlib.BoundLogger:
        """Per-crawler 命名空间 logger (amane.crawlers.{name}), 懒加载."""
        try:
            return self._logger
        except AttributeError:
            self._logger = structlog.get_logger(f"amane.crawlers.{self.name}")
            return self._logger

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        """
        根据查询条件搜索并抓取元数据.

        默认实现: _search() 找到 URL, 然后 _scrape() 解析.
        特殊爬虫可直接 override 此方法.

        Template Method 内置日志: search/scrape 各阶段自动记录.
        子类无需重复打日志, 除非有特殊信息.

        Args:
            query: 结构化搜索查询.
            options: 按次可变选项 (如语言).

        Returns:
            成功返回 MediaMetadata, 失败返回 None.
        """
        number = query.number
        lang = options.language if options else None
        t0 = time.monotonic()

        url = await self._search(query, options)
        if not url:
            self.logger.warning("search miss", number=number, language=lang)
            return None
        self.logger.info("search hit", number=number, url=url, language=lang)

        result = await self._scrape(url, options)
        elapsed = round(time.monotonic() - t0, 2)
        if result is None:
            self.logger.warning("scrape failed", number=number, url=url, duration_s=elapsed)
        else:
            self.logger.info("scrape ok", number=number, title=result.title, duration_s=elapsed)
        return result

    @abstractmethod
    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """搜索详情页; 命中返回 URL, 未命中返回 None."""
        ...

    @abstractmethod
    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        """获取详情页并解析元数据; 失败返回 None."""
        ...
