"""爬虫 HTTP 封装. ``get_html`` / ``get_rendered`` 命中拦截页抛 ``SourceError``.

按来源的浏览器策略经 ``for_source`` 派生绑定来源的视图: ``auto`` (默认) 先直连, 首次命中 Cloudflare
挑战后该来源改用浏览器并保持; ``always`` 一律渲染, ``off`` 一律直连. ``get_json`` 不做 HTML 拦截启发式.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import SiteConfig
    from ..enums import BrowserBackendName
    from ..net.browser import BrowserClient
    from ..net.http import WebClient

from ..enums import BrowserMode
from ..net.connectivity import ConnectivityOutcome, probe_get
from ..net.errors import FailureKind, FailureReason, RequestError, RequestFailure, SourceError, classify_block


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
        self._browser_mode = browser_mode
        self._browser_backend = browser_backend
        self._browser_timeout = browser_timeout
        # auto 已切到浏览器的 scope; 同一来源的派生视图共享, 避免每个视图重新撞盾.
        self._browser_required = browser_required if browser_required is not None else set()

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
            browser_required=self._browser_required,
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
    ) -> str:
        if self._should_render(url):
            return await self.get_rendered(url, headers=headers, cookies=cookies)
        text = await self.get_text(url, headers=headers, cookies=cookies, encoding=encoding)
        reason = classify_block(text)
        if reason is None:
            return text
        if (
            reason is FailureReason.CLOUDFLARE_CHALLENGE
            and self._browser_mode is BrowserMode.AUTO
            and self._browser_available()
        ):
            self._browser_required.add(self._scope(url))
            return await self.get_rendered(url, headers=headers, cookies=cookies)
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

    async def get_rendered(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> str:
        # 未配置后端或抓取失败抛 RequestError; 拦截页抛 SourceError.
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
        """按本视图的请求形态探测: 渲染视图走浏览器, 否则单次 HTTP GET; auto 命中挑战后切换."""
        if self._should_render(url):
            return await self._render_check(url, headers=headers, cookies=cookies)
        outcome = await probe_get(self._web, url, cookies=cookies, headers=headers)
        if (
            outcome.reason is FailureReason.CLOUDFLARE_CHALLENGE
            and self._browser_mode is BrowserMode.AUTO
            and self._browser_available()
        ):
            self._browser_required.add(self._scope(url))
            return await self._render_check(url, headers=headers, cookies=cookies)
        return outcome

    async def _render_check(
        self, url: str, *, headers: dict[str, str] | None, cookies: dict[str, str] | None
    ) -> ConnectivityOutcome:
        try:
            await self.get_rendered(url, headers=headers, cookies=cookies)
        except SourceError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=exc.url or url, http_status=exc.http_status)
        return ConnectivityOutcome.ok(url, None)

    def _scope(self, url: str) -> str:
        return self._source or urlparse(url).hostname or url

    def _should_render(self, url: str) -> bool:
        if self._browser_mode is BrowserMode.ALWAYS:
            return True
        return self._browser_mode is BrowserMode.AUTO and self._scope(url) in self._browser_required

    def _browser_available(self) -> bool:
        return self._browser is not None and self._browser.resolve(self._browser_backend) is not None

    async def download(self, url: str, dest: Path) -> bool:
        # 失败返回 False, 不抛异常.
        return await self._web.download(url, dest)
