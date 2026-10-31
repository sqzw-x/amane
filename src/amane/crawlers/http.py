"""爬虫 HTTP 封装: HTML 按来源策略在直连与浏览器之间路由.

``get_html`` 是唯一入口: ``use_browser=auto`` 先直连, 首次命中 Cloudflare 挑战后该来源改用浏览器并保持;
``always`` 一律渲染; ``off`` 一律直连. 判定集中在 ``RenderPolicy``, 派生视图共享已切换的 scope.
``get_json`` / ``get_bytes`` / ``download`` 永远直连, 不走 HTML 拦截启发式.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from ..enums import BrowserMode
from ..net.connectivity import ConnectivityOutcome, probe_get
from ..net.errors import FailureKind, FailureReason, RequestError, RequestFailure, SourceError, classify_block

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ..config import SiteConfig
    from ..enums import BrowserBackendName
    from ..net.browser import BrowserClient
    from ..net.http import WebClient

logger = structlog.get_logger()

# 渲染路径收尾前的网络静默上限: 挑战页与长轮询页面永远不会 idle, 到点就按当前正文继续.
_NETWORK_IDLE_TIMEOUT_MS = 3000.0


@dataclass
class RenderPolicy:
    """按来源的浏览器策略; 派生视图共享同一个 ``switched`` 集合."""

    mode: BrowserMode
    switched: set[str]
    available: Callable[[], bool]

    def should_render(self, scope: str) -> bool:
        if self.mode is BrowserMode.ALWAYS:
            return True
        return self.mode is BrowserMode.AUTO and scope in self.switched

    def escalate(self, scope: str, reason: FailureReason | None) -> bool:
        """直连结果命中 Cloudflare 挑战且策略允许时, 记下该 scope 并返回 True."""
        if reason is not FailureReason.CLOUDFLARE_CHALLENGE:
            return False
        if self.mode is not BrowserMode.AUTO or not self.available():
            return False
        self.switched.add(scope)
        return True


class HttpClient:
    """构造函数注入 WebClient / BrowserClient. 按来源策略由 ``for_source`` 派生."""

    def __init__(
        self,
        web: WebClient,
        browser: BrowserClient | None = None,
        *,
        source: str | None = None,
        browser_mode: BrowserMode = BrowserMode.AUTO,
        browser_backend: BrowserBackendName | None = None,
        browser_timeout: float = 30000.0,
        browser_required: set[str] | None = None,
    ):
        self._web = web
        self._browser = browser
        self._source = source
        self._browser_backend = browser_backend
        self._browser_timeout = browser_timeout
        self._policy = RenderPolicy(
            mode=browser_mode,
            switched=browser_required if browser_required is not None else set(),
            available=lambda: browser is not None and browser.resolve(browser_backend) is not None,
        )

    @property
    def web_client(self) -> WebClient:
        """Shared low-level client exposed to trusted source plugins."""
        return self._web

    def for_source(self, source: str, config: SiteConfig | None) -> HttpClient:
        """派生绑定来源的视图; 无来源配置时返回原对象."""
        if config is None:
            return self
        return HttpClient(
            self._web,
            self._browser,
            source=source,
            browser_mode=config.use_browser,
            browser_backend=config.browser_backend,
            browser_timeout=self._browser_timeout,
            browser_required=self._policy.switched,
        )

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
    ) -> str:
        return await self._web.get_text(url, headers=headers, cookies=cookies, encoding=encoding)

    async def get_html(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        wait_for: str | None = None,
    ) -> str:
        """取 HTML; ``wait_for`` 只在渲染路径生效 (等元素可见), 直连时忽略."""
        scope = self._scope(url)
        if self._policy.should_render(scope):
            return await self._render(url, headers=headers, cookies=cookies, wait_for=wait_for)
        text = await self.get_text(url, headers=headers, cookies=cookies, encoding=encoding)
        reason = classify_block(text)
        if reason is None:
            if wait_for is not None:
                logger.debug("wait_for ignored on direct response", url=url, selector=wait_for)
            return text
        if self._policy.escalate(scope, reason):
            return await self._render(url, headers=headers, cookies=cookies, wait_for=wait_for)
        raise SourceError(reason, detail=url)

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Any:
        return await self._web.get_json(url, headers=headers, cookies=cookies)

    async def get_bytes(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        return await self._web.get_bytes(url, headers=headers)

    async def post_json(
        self,
        url: str,
        *,
        json: Any,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._web.post_json(url, json=json, headers=headers)

    async def _render(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
        network_idle: bool = True,
    ) -> str:
        # 未配置后端或获取失败抛 RequestError; 拦截页抛 SourceError.
        if self._browser is None:
            raise RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message="browser backend disabled"))
        await self._web.acquire(url)
        html, failure = await self._browser.get_page(
            url,
            scope=self._scope(url),
            backend=self._browser_backend,
            cookies=cookies,
            headers=headers,
            wait_for=wait_for,
            timeout=timeout if timeout is not None else self._browser_timeout,
            # 有选择器时它就是就绪信号; 没有才退回到有上限的网络静默. 探测不必等静默.
            network_idle_timeout=None if wait_for is not None or not network_idle else _NETWORK_IDLE_TIMEOUT_MS,
        )
        if html is None:
            raise RequestError(
                url, failure or RequestFailure(kind=FailureKind.UNEXPECTED, message="browser fetch failed")
            )
        reason = classify_block(html)
        if reason is not None:
            raise SourceError(reason, detail=url)
        return html

    async def check(
        self, url: str, *, headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None
    ) -> ConnectivityOutcome:
        """按本视图的请求形态探测: 渲染视图经浏览器, 否则单次 HTTP GET; auto 命中挑战后切换."""
        scope = self._scope(url)
        if self._policy.should_render(scope):
            return await self._check_rendered(url, headers=headers, cookies=cookies)
        outcome = await probe_get(self._web, url, cookies=cookies, headers=headers)
        if self._policy.escalate(scope, outcome.reason):
            return await self._check_rendered(url, headers=headers, cookies=cookies)
        return outcome

    async def _check_rendered(
        self, url: str, *, headers: dict[str, str] | None, cookies: dict[str, str] | None
    ) -> ConnectivityOutcome:
        try:
            await self._render(url, headers=headers, cookies=cookies, network_idle=False)
        except SourceError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=exc.url or url, http_status=exc.http_status)
        return ConnectivityOutcome.ok(url, None)

    def _scope(self, url: str) -> str:
        return self._source or urlparse(url).hostname or url

    async def download(self, url: str, dest: Path) -> bool:
        # 失败返回 False, 不抛异常.
        return await self._web.download(url, dest)
