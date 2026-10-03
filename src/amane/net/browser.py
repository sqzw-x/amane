"""浏览器后端: 协议、按来源会话复用与失败映射.

``HttpClient.get_rendered`` 经 ``BrowserClient`` 协议调用; 本地引擎与外部 solver 服务可互换.
后端不感知爬虫层: 结果以 ``BrowserPageResult`` 返回, 失败语义由 ``RequestFailure`` 表达, 由
``HttpClient`` 统一分类. 协议中的 ``timeout`` 与 ``BrowserPool`` 的超时参数均为毫秒 (与 Playwright 一致).
"""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Protocol, TypedDict

import structlog

from ..enums import BrowserBackendName
from .errors import FailureKind, FailureReason, RequestError, RequestFailure, classify_block

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from .http import WebClient

logger = structlog.get_logger()

BrowserPageResult = tuple[str | None, RequestFailure | None]
"""(html, failure): 成功时 failure 为 None, 失败时 html 为 None."""

# 挑战页轮询间隔与浏览器空闲关闭时限 (秒).
_CHALLENGE_POLL_S = 1.0
_IDLE_TIMEOUT_S = 600.0
# 同一后端的并发页数上限. 限速由 RateLimiters 承担, 这里只约束浏览器资源.
_MAX_CONCURRENCY = 2
# solver 的 HTTP 调用在服务端等待挑战之外留出的余量 (秒), 以及会话建立 / 销毁的调用时限.
_SOLVER_HTTP_SLACK_S = 10.0
_SOLVER_SESSION_TIMEOUT_S = 15.0


class BrowserBackend(Protocol):
    """单个浏览器引擎."""

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult: ...

    async def close(self) -> None: ...


class BrowserClient(Protocol):
    """``HttpClient`` 依赖的浏览器通道: 按后端名解析实际引擎, 调用方不感知实现."""

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        backend: BrowserBackendName | None = None,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult: ...

    async def close(self) -> None: ...


class BrowserPool:
    """按配置解析并缓存后端实例; 引擎惰性启动, ``off`` 不产生任何实例.

    来源覆盖优先于全局: ``SiteConfig.browser_backend`` 为 ``off`` 表示该来源显式禁用浏览器.
    solver 经注入的 HTTP 通道出站, 因此复用本实例的调用方在热重建后须调用 ``rebind_web_client``.
    """

    def __init__(
        self,
        *,
        default_backend: BrowserBackendName,
        solver_url: str,
        proxy: str | None,
        web_client: WebClient,
        timeout_ms: float,
    ) -> None:
        self._default = default_backend
        self._solver_url = solver_url
        self._proxy = proxy
        self._web = web_client
        self._timeout_ms = timeout_ms
        self._instances: dict[BrowserBackendName, BrowserBackend] = {}

    @property
    def default_backend(self) -> BrowserBackendName:
        return self._default

    def resolve(self, override: BrowserBackendName | None) -> BrowserBackendName | None:
        """来源覆盖优先; 覆盖为 ``off`` 表示显式禁用, 结果为 None."""
        if override is not None:
            return None if override is BrowserBackendName.OFF else override
        return None if self._default is BrowserBackendName.OFF else self._default

    def rebind_web_client(self, web_client: WebClient) -> None:
        """热重建复用本实例时同步新的 HTTP 通道 (solver 经它出站)."""
        self._web = web_client

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        backend: BrowserBackendName | None = None,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult:
        name = self.resolve(backend)
        if name is None:
            return None, RequestFailure(kind=FailureKind.UNEXPECTED, message="browser backend disabled")
        instance = self._instances.get(name)
        if instance is None:
            instance = self._create(name)
            self._instances[name] = instance
        return await instance.get_page(
            url,
            scope=scope,
            cookies=cookies,
            headers=headers,
            wait_for=wait_for,
            timeout=timeout if timeout is not None else self._timeout_ms,
        )

    def _create(self, name: BrowserBackendName) -> BrowserBackend:
        match name:
            case BrowserBackendName.PATCHRIGHT:
                return PatchrightBackend(proxy=self._proxy, default_timeout=self._timeout_ms)
            case BrowserBackendName.CAMOUFOX:
                return CamoufoxBackend(proxy=self._proxy, default_timeout=self._timeout_ms)
            case BrowserBackendName.SOLVER:
                return SolverBackend(
                    web_provider=lambda: self._web,
                    url=self._solver_url,
                    default_timeout=self._timeout_ms,
                )
            case _:
                raise ValueError(f"unsupported browser backend: {name}")

    async def close(self) -> None:
        instances = list(self._instances.values())
        self._instances.clear()
        for instance in instances:
            await instance.close()


class _ChallengeUnresolved(Exception):
    """导航结束仍在挑战页: 由 ``_LocalBackend`` 转成带原因的失败."""


_WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]


class _Cookie(TypedDict, total=False):
    """playwright 系 ``SetCookieParam`` 的结构镜像.

    引擎是可选依赖, 协议不能直接引用其类型; 字段须与引擎定义保持一致才能结构匹配.
    """

    name: str
    value: str
    url: str | None
    domain: str | None
    path: str | None
    expires: float | None
    httpOnly: bool | None
    secure: bool | None
    sameSite: Literal["Lax", "None", "Strict"] | None
    partitionKey: str | None


class _PageLike(Protocol):
    """``_LocalBackend`` 使用的页面能力: 导航, 读取正文与等待选择器."""

    async def goto(self, url: str, *, timeout: float, wait_until: _WaitUntil) -> object: ...

    async def content(self) -> str: ...

    async def wait_for_selector(self, selector: str, *, timeout: float) -> object: ...

    async def close(self) -> None: ...


class _ContextLike(Protocol):
    """``_LocalBackend`` 使用的 context 能力: 注入 cookie / 请求头, 创建页面."""

    async def add_cookies(self, cookies: Sequence[_Cookie]) -> None: ...

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None: ...

    async def new_page(self) -> _PageLike: ...


class _BrowserLike(Protocol):
    """``_LocalBackend`` 使用的浏览器能力: 按来源创建独立 context."""

    async def new_context(self) -> _ContextLike: ...


class _LocalBackend(ABC):
    """playwright 系本地引擎共用: 会话复用, 挑战等待, 并发与空闲释放.

    每个来源一个 context: 同一来源的连续请求复用 clearance; 不同来源互不污染 cookie.
    返回给调用方的正文仍由 ``HttpClient`` 二次分类, 这里只等待挑战消解.
    """

    def __init__(self, *, proxy: str | None, default_timeout: float, idle_timeout: float = _IDLE_TIMEOUT_S) -> None:
        self._proxy = proxy
        self._default_timeout = default_timeout
        self._idle_timeout = idle_timeout
        self._stack: AsyncExitStack | None = None
        self._browser: _BrowserLike | None = None
        self._contexts: dict[str, _ContextLike] = {}
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._launch_lock = asyncio.Lock()
        self._active = 0
        self._idle_task: asyncio.Task[None] | None = None

    @abstractmethod
    async def _launch(self, stack: AsyncExitStack) -> _BrowserLike:
        """启动浏览器, 并把退出清理压入 ``stack``."""

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult:
        effective = timeout if timeout is not None else self._default_timeout
        self._active += 1
        self._schedule_idle()
        try:
            async with self._semaphore:
                return await self._fetch(
                    url,
                    scope=scope,
                    cookies=cookies,
                    headers=headers,
                    wait_for=wait_for,
                    timeout=effective,
                )
        finally:
            self._active -= 1

    async def _fetch(
        self,
        url: str,
        *,
        scope: str,
        cookies: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        wait_for: str | None,
        timeout: float,
    ) -> BrowserPageResult:
        page: _PageLike | None = None
        try:
            context = await self._context(scope)
            # 引擎启动 (含 camoufox 首次下载) 不计入单次渲染时限.
            deadline = time.monotonic() + timeout / 1000
            if cookies:
                context_cookies: list[_Cookie] = [
                    {"name": name, "value": value, "url": url} for name, value in cookies.items()
                ]
                await context.add_cookies(context_cookies)
            if headers:
                await context.set_extra_http_headers(dict(headers))
            page = await context.new_page()
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            content = await self._settle_challenge(page, deadline)
            if wait_for is not None:
                remaining = max(1.0, (deadline - time.monotonic()) * 1000)
                await page.wait_for_selector(wait_for, timeout=remaining)
                content = await page.content()
            return content, None
        except _ChallengeUnresolved:
            return None, RequestFailure(
                kind=FailureKind.UNEXPECTED,
                message=f"cloudflare challenge not resolved: {url}",
                reason=FailureReason.CLOUDFLARE_CHALLENGE,
            )
        except Exception as exc:
            if _is_timeout(exc):
                return None, RequestFailure(kind=FailureKind.TIMEOUT, message=f"browser timeout: {url}")
            logger.error("browser page fetch failed", url=url, error=str(exc))
            return None, RequestFailure(
                kind=FailureKind.UNEXPECTED,
                message=f"browser error: {type(exc).__name__}: {exc}",
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception as exc:
                    logger.debug("browser page close failed (ignored)", url=url, error=str(exc))

    async def _settle_challenge(self, page: _PageLike, deadline: float) -> str:
        """轮询正文直到挑战消解; 超时抛 ``_ChallengeUnresolved``.

        导航瞬间读正文可能撞上框架重建; 读失败不视为空页, 计入等待直到超时.
        """
        while True:
            try:
                content: str | None = await page.content()
            except Exception:
                content = None
            if content is not None and classify_block(content) is not FailureReason.CLOUDFLARE_CHALLENGE:
                return content
            if time.monotonic() >= deadline:
                raise _ChallengeUnresolved
            await asyncio.sleep(_CHALLENGE_POLL_S)

    async def _ensure_browser(self) -> _BrowserLike:
        browser = self._browser
        if browser is not None:
            return browser
        async with self._launch_lock:
            browser = self._browser
            if browser is None:
                stack = AsyncExitStack()
                try:
                    browser = await self._launch(stack)
                except BaseException:
                    await stack.aclose()
                    raise
                self._browser = browser
                self._stack = stack
        return browser

    async def _context(self, scope: str) -> _ContextLike:
        context = self._contexts.get(scope)
        if context is None:
            browser = await self._ensure_browser()
            context = await browser.new_context()
            self._contexts[scope] = context
        return context

    def _schedule_idle(self) -> None:
        if self._idle_timeout <= 0:
            return
        if self._idle_task is not None:
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(self._idle_close())

    async def _idle_close(self) -> None:
        try:
            await asyncio.sleep(self._idle_timeout)
        except asyncio.CancelledError:
            return
        if self._active:
            self._schedule_idle()
            return
        await self._close_browser()

    async def _close_browser(self, *, force: bool = False) -> None:
        async with self._launch_lock:
            if self._active and not force:
                return
            self._contexts.clear()
            stack, self._stack = self._stack, None
            self._browser = None
            if stack is not None:
                await stack.aclose()

    async def close(self) -> None:
        task, self._idle_task = self._idle_task, None
        if task is not None:
            task.cancel()
        await self._close_browser(force=True)


class PatchrightBackend(_LocalBackend):
    """patchright 驱动的系统 Chrome; ``AMANE_SHOW_BROWSER`` 置位时强制有头."""

    async def _launch(self, stack: AsyncExitStack) -> _BrowserLike:
        from patchright.async_api import async_playwright

        playwright = await stack.enter_async_context(async_playwright())
        browser = await playwright.chromium.launch(
            channel="chrome",
            headless=os.getenv("AMANE_SHOW_BROWSER") is None,
            args=["--disable-blink-features=AutomationControlled"],
            proxy={"server": self._proxy} if self._proxy else None,
        )
        stack.push_async_callback(browser.close)
        return browser


class _CamoufoxOptions(TypedDict):
    """``AsyncCamoufox`` 的启动参数."""

    headless: bool
    humanize: bool
    locale: str
    proxy: NotRequired[dict[str, str]]


class CamoufoxBackend(_LocalBackend):
    """stealth Firefox; 浏览器二进制在首次启动时下载."""

    async def _launch(self, stack: AsyncExitStack) -> _BrowserLike:
        from camoufox.async_api import AsyncCamoufox
        from playwright.async_api import Browser

        options: _CamoufoxOptions = {
            "headless": os.getenv("AMANE_SHOW_BROWSER") is None,
            "humanize": True,
            "locale": "ja-JP",
        }
        if self._proxy:
            options["proxy"] = {"server": self._proxy}
        browser = await stack.enter_async_context(AsyncCamoufox(**options))
        # 未启用 persistent_context, 返回值必为 Browser.
        assert isinstance(browser, Browser), "camoufox persistent context is not supported"
        return browser


class SolverBackend:
    """FlareSolverr 兼容服务: HTML 取自 ``solution.response``, 会话按来源复用.

    只支持 ``request.get``; 请求头由 solver 自行决定, 因此 ``headers`` 不参与转发 (本地后端支持).
    """

    def __init__(self, *, web_provider: Callable[[], WebClient], url: str, default_timeout: float) -> None:
        self._web_provider = web_provider
        self._url = url.rstrip("/")
        self._default_timeout = default_timeout
        self._sessions: set[str] = set()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._lock = asyncio.Lock()

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult:
        effective = timeout if timeout is not None else self._default_timeout
        async with self._semaphore:
            try:
                await self._ensure_session(scope)
                data = await self._request_get(url, scope, effective, cookies)
            except RequestError as exc:
                return None, exc.failure or RequestFailure(kind=FailureKind.UNEXPECTED, message=exc.message)
            if not _solver_ok(data):
                message = _solver_message(data)
                if _is_missing_session(message):
                    self._sessions.discard(scope)
                    try:
                        await self._ensure_session(scope)
                        data = await self._request_get(url, scope, effective, cookies)
                    except RequestError as exc:
                        return None, exc.failure or RequestFailure(kind=FailureKind.UNEXPECTED, message=exc.message)
                    if not _solver_ok(data):
                        return None, _solver_failure(_solver_message(data))
                else:
                    return None, _solver_failure(message)

            solution = data.get("solution")
            if not isinstance(solution, dict):
                return None, RequestFailure(kind=FailureKind.UNEXPECTED, message="solver response has no solution")
            html = solution.get("response")
            if not isinstance(html, str):
                return None, RequestFailure(kind=FailureKind.UNEXPECTED, message="solver solution has no response")
            return html, None

    async def _request_get(
        self, url: str, scope: str, timeout_ms: float, cookies: Mapping[str, str] | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "session": scope,
            "maxTimeout": int(timeout_ms),
        }
        if cookies:
            payload["cookies"] = [{"name": name, "value": value} for name, value in cookies.items()]
        return await self._post(payload, timeout_s=timeout_ms / 1000 + _SOLVER_HTTP_SLACK_S)

    async def _ensure_session(self, scope: str) -> None:
        if scope in self._sessions:
            return
        async with self._lock:
            if scope in self._sessions:
                return
            data = await self._post(
                {"cmd": "sessions.create", "session": scope},
                timeout_s=_SOLVER_SESSION_TIMEOUT_S,
            )
            message = _solver_message(data)
            # 同名会话已存在同样可用, 不视为失败.
            if not _solver_ok(data) and not _is_existing_session(message):
                raise RequestError(self._url, _solver_failure(message))
            self._sessions.add(scope)

    async def _post(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        web = self._web_provider()
        resp = await web.request(
            "POST", f"{self._url}/v1", json=payload, use_proxy=False, timeout=timeout_s, max_attempts=1
        )
        try:
            data = resp.json()
        except Exception as exc:
            raise RequestError(
                self._url,
                RequestFailure(kind=FailureKind.UNEXPECTED, message=f"solver response is not JSON: {exc}"),
            ) from exc
        if not isinstance(data, dict):
            raise RequestError(
                self._url, RequestFailure(kind=FailureKind.UNEXPECTED, message="solver response is not an object")
            )
        return data

    async def close(self) -> None:
        sessions = list(self._sessions)
        self._sessions.clear()
        for scope in sessions:
            try:
                await self._post({"cmd": "sessions.destroy", "session": scope}, timeout_s=_SOLVER_SESSION_TIMEOUT_S)
            except Exception as exc:
                logger.debug("solver session destroy failed (ignored)", session=scope, error=str(exc))


def _solver_ok(data: dict[str, Any]) -> bool:
    return data.get("status") == "ok"


def _solver_message(data: dict[str, Any]) -> str:
    message = data.get("message")
    return message if isinstance(message, str) and message else "solver error"


def _solver_failure(message: str) -> RequestFailure:
    lower = message.lower()
    if "timeout" in lower:
        return RequestFailure(kind=FailureKind.TIMEOUT, message=message)
    if "challenge" in lower:
        return RequestFailure(kind=FailureKind.UNEXPECTED, message=message, reason=FailureReason.CLOUDFLARE_CHALLENGE)
    return RequestFailure(kind=FailureKind.UNEXPECTED, message=message)


def _is_missing_session(message: str) -> bool:
    lower = message.lower()
    return "session" in lower and ("not found" in lower or "does not exist" in lower or "unknown" in lower)


def _is_existing_session(message: str) -> bool:
    lower = message.lower()
    return "session" in lower and ("already" in lower or "exist" in lower)


def _is_timeout(exc: BaseException) -> bool:
    """playwright 的 TimeoutError 不继承内建异常, 且可选依赖不能顶层导入, 按类名识别."""
    return isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"
