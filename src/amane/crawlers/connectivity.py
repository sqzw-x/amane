"""来源连通性探测的编排.

探测目标与判定都由来源自己声明 (``Crawler.check_connectivity`` / ``ActorCrawler.check_connectivity`` /
插件 provider 的同名方法), 本模块不认识任何具体站点, 只做三件事: 读取来源清单 → 并发调用 → 捕获异常.
结果类型与传输原语见 ``amane.net.connectivity``.

目标清单默认取当前热配置**会真正请求**的来源 (各类型路由的并集 + 演员档案 / 头像来源), 因此探测范围与
刮削范围一致, 不会把没在用的站点混进结果; 显式传入 ``source_ids`` 时只探这些 (供 UI 单点重试).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import structlog

from ..net.connectivity import ConnectivityOutcome, ConnectivityStatus
from ..net.errors import FailureReason, SourceError
from .actor import actor_registry
from .registry import registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..config import HotSettings
    from ..plugins.manager import PluginManager
    from .factory import CrawlerFactory

logger = structlog.get_logger()


class SourceKind(StrEnum):
    """来源类别, 供展示分组用. 插件来源的 ID 由插件命名空间决定, 不能从名字反推."""

    FILM = "film"
    ACTOR = "actor"
    PLUGIN = "plugin"


class ConnectivityProbe(Protocol):
    """可探测的来源. 内置爬虫与插件适配器都满足它, 二者没有公共基类."""

    async def check_connectivity(self) -> ConnectivityOutcome | None: ...


@dataclass(frozen=True, slots=True)
class SourceCheck:
    """一个来源的探测结果.

    ``elapsed_ms`` 只计探测本身, 不含取来源实例的时间; ``SKIPPED`` 时为 ``None``.
    """

    source_id: str
    name: str
    kind: SourceKind
    outcome: ConnectivityOutcome
    elapsed_ms: int | None


class ConnectivityChecker:
    """用 ``CrawlerFactory`` 当前的网络栈探测来源, 因此结果与当前配置一致."""

    def __init__(
        self,
        factory: CrawlerFactory,
        hot: HotSettings,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        self._factory = factory
        self._hot = hot
        self._plugins = plugin_manager

    async def check(self, source_ids: Sequence[str] | None = None) -> list[SourceCheck]:
        """并发探测全部目标; 单个来源的失败只体现在自己的结果里, 不影响其它来源."""
        ids = list(dict.fromkeys(source_ids)) if source_ids else self._configured_ids()
        return list(await asyncio.gather(*(self._check_one(source_id) for source_id in ids)))

    def _configured_ids(self) -> list[str]:
        chain_ids: list[str] = []
        for sites in self._hot.scraping.content_routes.values():
            chain_ids.extend(sites)
        chain_ids.extend(str(site) for site in self._hot.actor_scraping.profile_sites)
        chain_ids.extend(str(site) for site in self._hot.actor_scraping.image_sites)
        return list(dict.fromkeys(chain_ids))

    async def _check_one(self, source_id: str) -> SourceCheck:
        probe, kind, name = await self._resolve(source_id)
        if probe is None:
            outcome = ConnectivityOutcome.skipped("来源不存在或未启用")
            return SourceCheck(source_id, name, kind, outcome, None)
        t0 = time.monotonic()
        outcome = await self._run(probe)
        elapsed = round((time.monotonic() - t0) * 1000)
        # ``SKIPPED`` 一律不给毫秒数: 没探测就没有耗时, 界面显示「无法探测 + 0 ms」会自相矛盾.
        return SourceCheck(
            source_id, name, kind, outcome, None if outcome.status is ConnectivityStatus.SKIPPED else elapsed
        )

    async def _resolve(self, source_id: str) -> tuple[ConnectivityProbe | None, SourceKind, str]:
        """插件来源优先: 插件 ID 不会与内置 ``SiteName`` 重名, 但判定顺序仍固定, 便于阅读."""
        if self._plugins is not None and source_id in self._plugins.plugin_ids():
            descriptor = self._plugins.descriptor(source_id)
            return await self._factory.get(source_id), SourceKind.PLUGIN, descriptor.name if descriptor else source_id
        if source_id in registry.sites():
            return await self._factory.get(source_id), SourceKind.FILM, source_id
        if source_id in actor_registry.sites():
            return await self._factory.get_actor(source_id), SourceKind.ACTOR, source_id
        return None, SourceKind.FILM, source_id

    async def _run(self, probe: ConnectivityProbe) -> ConnectivityOutcome:
        """捕获来源抛出的异常. 未声明的 ``None`` 表示该插件不探测, 不计入失败."""
        try:
            outcome = await probe.check_connectivity()
        except SourceError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=exc.url, http_status=exc.http_status)
        except Exception as exc:
            # 异常全文可能带上游地址或密钥, 只写入日志; 结果里只报告类型名.
            logger.exception("connectivity check failed", error=type(exc).__name__)
            return ConnectivityOutcome.failed(FailureReason.UNEXPECTED, detail=type(exc).__name__)
        if outcome is None:
            return ConnectivityOutcome.skipped("该来源未声明探测方式")
        return outcome
