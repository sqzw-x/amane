"""爬虫用 HTTP 客户端: WebClient / BrowserClient 的薄封装, 失败时抛 SourceError."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ..net.http import BrowserClient, WebClient

from ..net.errors import RequestError, SourceError, classify_block


class HttpClient:
    """
    爬虫用 HTTP 客户端.

    封装 WebClient (curl_cffi) 和可选的 BrowserClient (patchright).
    设计为构造函数注入 - 可传入真实或模拟客户端.

        text = await client.get_html(url)  # HTML: 拦截页抛 SourceError
        data = await client.get_json(url)  # JSON API, 不做 HTML 启发式
    """

    def __init__(self, web: WebClient, browser: BrowserClient | None = None):
        self._web = web
        self._browser = browser

    @property
    def web_client(self) -> WebClient:
        """Shared low-level client exposed to trusted source plugins."""
        return self._web

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
    ) -> str:
        """GET 请求并返回响应文本. 不做拦截页判定."""
        return await self._web.get_text(url, headers=headers, cookies=cookies, encoding=encoding)

    async def get_html(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
    ) -> str:
        """GET HTML; 命中拦截/空页启发式则抛 SourceError."""
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
        """GET 请求并返回解析后的 JSON."""
        return await self._web.get_json(url, headers=headers, cookies=cookies)

    async def get_bytes(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        """GET 请求并返回原始字节."""
        return await self._web.get_bytes(url, headers=headers)

    async def post_json(
        self,
        url: str,
        *,
        json: Any,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST JSON 载荷 (object 或 array) 并返回解析后的响应."""
        return await self._web.post_json(url, json=json, headers=headers)

    async def get_rendered(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        timeout: float = 30000,
    ) -> str:
        """通过无头浏览器获取 JS 渲染后的页面; 未配置浏览器或抓取失败抛 RequestError, 拦截页抛 SourceError."""
        if self._browser is None:
            raise RequestError(url, "BrowserClient not configured")
        html, err = await self._browser.get_page(url, wait_for=wait_for, timeout=timeout)
        if html is None:
            raise RequestError(url, err)
        reason = classify_block(html)
        if reason is not None:
            raise SourceError(reason, detail=url)
        return html

    async def download(self, url: str, dest: Path) -> bool:
        """下载文件到本地路径. 成功 True, 失败 False (不抛出)."""
        return await self._web.download(url, dest)
