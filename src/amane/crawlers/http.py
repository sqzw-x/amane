"""爬虫 HTTP 封装. ``get_html`` / ``get_rendered`` 命中拦截页抛 ``SourceError``.

启用浏览器渲染的来源经 ``for_source`` 派生绑定来源的视图: ``get_html`` 自动改走渲染通道, 同一来源保持
单一请求形态. ``get_json`` 不做 HTML 拦截启发式.
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

from ..net.connectivity import ConnectivityOutcome, probe_get
from ..net.errors import FailureKind, RequestError, RequestFailure, SourceError, classify_block

# 未显式传 timeout 且未接线配置时的渲染时限 (毫秒).
_DEFAULT_BROWSER_TIMEOUT_MS = 30000.0


class HttpClient:
    """构造函数注入 WebClient / BrowserClient. 渲染视图由 ``for_source`` 派生."""

    def __init__(
        self,
        web: WebClient,
        browser: BrowserClient | None = None,
        *,
        source: str | None = None,
        use_browser: bool = False,
        browser_backend: BrowserBackendName | None = None,
        browser_timeout: float | None = None,
    ):
        self._web = web
        self._browser = browser
        self._source = source
        self._use_browser = use_browser
        self._browser_backend = browser_backend
        self._browser_timeout = browser_timeout if browser_timeout is not None else _DEFAULT_BROWSER_TIMEOUT_MS

    @property
    def web_client(self) -> WebClient:
        """Shared low-level client exposed to trusted source plugins."""
        return self._web

    def for_source(self, source: str, config: SiteConfig | None) -> HttpClient:
        """派生绑定来源的视图; 未启用浏览器渲染时返回原对象."""
        if config is None or not config.use_browser:
            return self
        return HttpClient(
            self._web,
            self._browser,
            source=source,
            use_browser=True,
            browser_backend=config.browser_backend,
            browser_timeout=self._browser_timeout,
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
        if self._use_browser:
            return await self.get_rendered(url, headers=headers, cookies=cookies)
        text = await self.get_text(url, headers=headers, cookies=cookies, encoding=encoding)
        reason = classify_block(text)
        if reason is not None:
            raise SourceError(reason, detail=url)
        return text

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
        # 未启用后端或抓取失败抛 RequestError; 拦截页抛 SourceError.
        if self._browser is None:
            raise RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message="browser backend disabled"))
        await self._web.acquire(url)
        scope = self._source or urlparse(url).hostname or url
        html, failure = await self._browser.get_page(
            url,
            scope=scope,
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
        """按本视图的请求形态探测: 渲染视图走浏览器, 否则单次 HTTP GET."""
        if not self._use_browser:
            return await probe_get(self._web, url, cookies=cookies, headers=headers)
        try:
            await self.get_rendered(url, headers=headers, cookies=cookies)
        except SourceError as exc:
            return ConnectivityOutcome.failed(exc.reason, url=exc.url or url, http_status=exc.http_status)
        return ConnectivityOutcome.ok(url, None)

    async def download(self, url: str, dest: Path) -> bool:
        # 失败返回 False, 不抛异常.
        return await self._web.download(url, dest)
