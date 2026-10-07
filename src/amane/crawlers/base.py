import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from ..enums import ActorGender
from ..net.connectivity import ConnectivityOutcome
from ..plugins.models import SourceCapability, SourceDescriptor, SourceTrait
from .http import HttpClient

if TYPE_CHECKING:
    from ..aggregate.engine import CrawlerLike
    from ..config import SiteConfig
    from ..enums import SiteName
    from .connectivity import ConnectivityProbe
    from .models import FetchOptions, MediaMetadata, SearchQuery


@dataclass
class RequestContext:
    base_url: str
    cookies: dict[str, str]


@dataclass
class CrawlerProfile:
    name: SiteName | str
    base_url: str
    urls: list[str] = field(default_factory=list)
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    # 该站静态资源校验同源 Referer 时置 True: 下载图片按 host 注入 ``https://{host}/``.
    same_origin_referer: bool = False
    # 空则影片爬虫视为 film_metadata; 演员爬虫必须显式声明 profile / image.
    capabilities: frozenset[SourceCapability] = field(default_factory=frozenset)
    # 行为开关: 引擎读到某个取值就改变调度或调用方式 (排段 / 多语言 / 文件指纹), 取值见 ``SourceTrait``.
    # 与 capabilities (来源提供什么) 分开.
    traits: frozenset[SourceTrait] = field(default_factory=frozenset)
    genders: frozenset[ActorGender] | None = None

    def effective_capabilities(self) -> frozenset[SourceCapability]:
        return self.capabilities or frozenset({SourceCapability.FILM_METADATA})

    def to_descriptor(self) -> SourceDescriptor:
        """引擎与配置读取的声明性事实都在这里产出, 新增 profile 字段时同步此处."""
        return SourceDescriptor(
            id=str(self.name),
            name=str(self.name),
            version="builtin",
            capabilities=frozenset(self.effective_capabilities()),
            urls=(*self.urls, self.base_url),
            traits=frozenset(str(trait) for trait in self.traits),
        )


class Crawler(ABC):
    """默认 ``_search`` → ``_scrape``. 无状态: SiteConfig 经方法参数注入, 不经构造函数.

    特殊源可直接 override ``fetch()``.
    """

    @classmethod
    @abstractmethod
    def profile(cls) -> CrawlerProfile: ...

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
            self._logger = structlog.get_logger(f"amane.crawlers.{self.name}")
            return self._logger

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
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

    async def check_connectivity(self) -> ConnectivityOutcome:
        """连通性自检: 缺省 GET ``base_url``, 用与刮削相同的视图 (渲染来源经浏览器, 其余直接请求).

        实际入口与 ``base_url`` 不同的来源覆盖本方法 (探测真实 API 主机, 或按前置条件报 ``skipped``).
        """
        return await self.client.check(self.base_url, cookies=self.cookies, headers=self.headers)

    @abstractmethod
    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None: ...

    @abstractmethod
    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None: ...


if TYPE_CHECKING:
    # 结构性协议没有运行期检查: 在此静态断言实例满足聚合引擎与连通性编排声明的协议.
    _crawler_like: type[CrawlerLike] = Crawler
    _connectivity_probe: type[ConnectivityProbe] = Crawler
