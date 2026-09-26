"""curl_cffi TLS 指纹模拟 + 限速 + 重试; 爬虫 / 图片 / Emby 等对外 HTTP 统一经此模块."""

import asyncio
import os
import random
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import aiofiles
import httpx2 as httpx
import structlog
from aiolimiter import AsyncLimiter
from curl_cffi import CurlError
from curl_cffi.requests import AsyncSession, BrowserTypeLiteral, Response

from .errors import FailureKind, RequestError, RequestFailure
from .recording import get_bound_http_recorder, reset_skip_http_body, set_skip_http_body

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from curl_cffi.requests.session import HttpMethod

    from ..config import SiteConfig

logger = structlog.get_logger()


@contextmanager
def _skip_body_recording() -> Iterator[None]:
    """get_bytes / download 跳过 body 落盘 (仅记 meta)."""
    token = set_skip_http_body(True)
    try:
        yield
    finally:
        reset_skip_http_body(token)


_IMPERSONATE_OPTIONS: tuple[BrowserTypeLiteral, ...] = (
    "chrome123",
    "chrome124",
    "chrome131",
    "chrome136",
    "firefox133",
    "firefox135",
)

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 503, 504})


class RateLimiters:
    """严格平滑限流: ``AsyncLimiter(1, 1/rate)``, 桶容量 1, 完全无突发."""

    _BUILTIN_HOSTS = frozenset({"127.0.0.1", "localhost"})
    _LOCALHOST_RATE = 300.0

    def __init__(self, default_rate: float = 5):
        self._default_rate = default_rate
        self._limiters: dict[str, AsyncLimiter] = {
            "127.0.0.1": _make_limiter(self._LOCALHOST_RATE),
            "localhost": _make_limiter(self._LOCALHOST_RATE),
        }

    @classmethod
    def from_config(
        cls,
        network_rate_limits: Mapping[str, float],
        site_configs: Mapping[str, SiteConfig],
        site_urls: Mapping[str, list[str]],
        *,
        source_rates: Mapping[str, float | None] | None = None,
        default_rate: float = 5,
    ) -> RateLimiters:
        """优先级: 全局 network.rate_limits > site_config.rate_limit > 默认."""
        instance = cls(default_rate=default_rate)

        # site_config.rate_limit (低优先级)
        for site_name, cfg in site_configs.items():
            if cfg.rate_limit is None:
                continue
            base_urls = list(site_urls.get(str(site_name), []))
            if cfg.base_url:
                base_urls.append(cfg.base_url)
            for url in base_urls:
                host = httpx.URL(url).host
                if host:
                    instance._limiters[host] = _make_limiter(cfg.rate_limit)

        # Plugin descriptors can provide a source-level default without being
        # forced into the core SiteConfig model.
        for source_name, rate in (source_rates or {}).items():
            if rate is None:
                continue
            for url in site_urls.get(str(source_name), []):
                host = httpx.URL(url).host
                if host and host not in instance._limiters:
                    instance._limiters[host] = _make_limiter(rate)

        # network.rate_limits (高优先级, 覆盖上一层)
        for host, rate in network_rate_limits.items():
            instance._limiters[host] = _make_limiter(rate)

        configured = len(instance._limiters) - len(cls._BUILTIN_HOSTS)
        if configured:
            logger.info("rate_limiters.created", configured_hosts=configured)

        return instance

    def get(self, host: str, *, rate: float | None = None) -> AsyncLimiter:
        if host not in self._limiters:
            self._limiters[host] = _make_limiter(rate or self._default_rate)
        return self._limiters[host]

    def set_rate(self, host: str, rate: float) -> None:
        self._limiters[host] = _make_limiter(rate)


def _make_limiter(rate: float) -> AsyncLimiter:
    """``AsyncLimiter(1, 1/rate)``: 桶容量 1, 无突发."""
    return AsyncLimiter(1, 1 / rate)


def _failure_body(resp: Response | None) -> bytes | None:
    if resp is None:
        return None
    try:
        content = resp.content
    except Exception:
        return None
    return content[:_FAILURE_BODY_LIMIT]


# 失败响应正文保留上限 (防大响应驻留内存)
_FAILURE_BODY_LIMIT = 64 * 1024


def _with_same_origin_referer(
    host: str | None, headers: dict[str, str] | None, hosts: frozenset[str]
) -> dict[str, str] | None:
    """host 命中 ``hosts`` 且调用方未给出 Referer 时补同源 Referer; 已有则保持原值.

    站点按 Referer 前缀匹配, 结尾斜杠属于匹配条件, 不可省略. 不修改传入的字典.
    """
    if host is None or host not in hosts:
        return headers
    if headers is not None and any(k.lower() == "referer" for k in headers):
        return headers
    return {**(headers or {}), "Referer": f"https://{host}/"}


class WebClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        max_clients: int = 50,
        limiters: RateLimiters,
        same_origin_referer_hosts: frozenset[str] = frozenset(),
    ):
        self._proxy = proxy
        self._timeout = timeout
        self._max_retries = max_retries
        self._limiters = limiters
        self._same_origin_referer_hosts = same_origin_referer_hosts
        self._session = AsyncSession(
            max_clients=max_clients,
            verify=False,
            max_redirects=20,
            timeout=timeout,
            impersonate=random.choice(_IMPERSONATE_OPTIONS),
        )

    async def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        use_proxy: bool = True,
        timeout: float | None = None,
        allow_redirects: bool = True,
        ok_statuses: frozenset[int] | None = None,
        max_attempts: int | None = None,
    ) -> Response:
        """``ok_statuses`` 额外视为成功 (例如 RSS 304), 不重试、不当失败. 重试用尽后抛 ``RequestError``.

        ``max_attempts`` 向下覆盖构造期的 ``max_retries``, 供一次性的探测使用. ``max_retries`` 实为总
        **尝试次数** (``3`` → 最多发 3 次请求), 名字为兼容既有配置保留; 两者都至少发一次请求, 配置 0
        表示不重试而不是一次都不发.
        """
        host = httpx.URL(url).host
        headers = _with_same_origin_referer(host, headers, self._same_origin_referer_hosts)
        await self._limiters.get(host).acquire()

        attempts = max(1, self._max_retries if max_attempts is None else min(max_attempts, self._max_retries))
        t0 = time.monotonic()
        failure: RequestFailure | None = None
        last_resp: Response | None = None
        for attempt in range(attempts):
            should_retry = False
            try:
                resp: Response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    cookies=cookies,
                    data=data,
                    json=json,
                    proxy=self._proxy if use_proxy else None,
                    timeout=timeout or self._timeout,
                    allow_redirects=allow_redirects,
                )
                last_resp = resp

                extra_ok = ok_statuses or frozenset()
                if (
                    resp.status_code < 300
                    or resp.status_code in extra_ok
                    or (resp.status_code in (301, 302, 307, 308) and resp.headers.get("Location"))
                ):
                    self._record_exchange(method, url, resp=resp, error=None, t0=t0)
                    return resp

                failure = RequestFailure(
                    kind=FailureKind.HTTP_STATUS,
                    status=resp.status_code,
                    message=f"HTTP {resp.status_code}",
                    body=_failure_body(resp),
                )
                should_retry = resp.status_code in _RETRYABLE_STATUS_CODES

            except CurlError as e:
                failure = RequestFailure(kind=FailureKind.CURL, message=f"curl error: {e}")
                should_retry = True
                last_resp = None
            except TimeoutError:
                failure = RequestFailure(kind=FailureKind.TIMEOUT, message="timeout")
                should_retry = True
                last_resp = None
            except Exception as e:
                failure = RequestFailure(kind=FailureKind.UNEXPECTED, message=f"unexpected: {type(e).__name__}: {e}")
                last_resp = None

            if not should_retry:
                break

            if attempt < attempts - 1:
                wait = attempt * 3 + 2 + random.uniform(-1, 1)
                logger.warning(
                    "request retry",
                    method=method,
                    url=url,
                    attempt=attempt + 1,
                    max_retries=attempts,
                    error=failure.message,
                    retry_in=wait,
                )
                await asyncio.sleep(wait)

        log_failed = logger.debug if get_bound_http_recorder() is not None else logger.error
        log_failed(
            "request failed",
            method=method,
            url=url,
            error=failure.message if failure else None,
            attempts=attempts,
            duration_s=round(time.monotonic() - t0, 2),
        )
        self._record_exchange(
            method, url, resp=last_resp, error=failure.message if failure else None, t0=t0, attempts=attempts
        )
        raise RequestError(url, failure)

    def _record_exchange(
        self,
        method: str,
        url: str,
        *,
        resp: Response | None,
        error: str | None,
        t0: float,
        attempts: int | None = None,
    ) -> None:
        rec = get_bound_http_recorder()
        if rec is None:
            return
        content_type: str | None = None
        status: int | None = None
        body: bytes | None = None
        if resp is not None:
            status = resp.status_code
            content_type = resp.headers.get("Content-Type") or resp.headers.get("content-type")
            try:
                body = resp.content
            except Exception:
                body = None
        rec.record_http(
            method=str(method),
            url=url,
            status=status,
            error=error,
            content_type=content_type,
            body=body,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            attempts=attempts,
        )

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> str:
        resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        try:
            resp.encoding = encoding
            return resp.text
        except Exception as e:
            raise RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"decode error: {e}")) from e

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> Any:
        resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        try:
            return resp.json()
        except Exception as e:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"JSON parse error: {e}")
            ) from e

    async def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> bytes:
        with _skip_body_recording():
            resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        return resp.content

    async def post_text(
        self,
        url: str,
        *,
        data: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> str:
        resp = await self.request(
            "POST", url, data=data, json=json, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        try:
            resp.encoding = encoding
            return resp.text
        except Exception as e:
            raise RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"decode error: {e}")) from e

    async def post_json(
        self,
        url: str,
        *,
        data: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> Any:
        resp = await self.request(
            "POST", url, data=data, json=json, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        try:
            return resp.json()
        except Exception as e:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"JSON parse error: {e}")
            ) from e

    async def _probe(self, url: str, *, use_proxy: bool) -> tuple[int | None, str]:
        """HEAD 探测 Content-Length 与重定向终址; 探测失败时终址回退为 ``url``."""
        try:
            resp = await self.request("HEAD", url, use_proxy=use_proxy)
        except RequestError:
            return None, url
        final_url = str(resp.url) if resp.url else url
        if resp.status_code >= 400:
            return None, final_url
        cl = resp.headers.get("Content-Length")
        try:
            return (int(cl) if cl else None), final_url
        except ValueError, TypeError:
            return None, final_url

    async def get_filesize(self, url: str, *, use_proxy: bool = True) -> int | None:
        size, _ = await self._probe(url, use_proxy=use_proxy)
        return size

    async def resolve_final_url(self, url: str, *, use_proxy: bool = True) -> str:
        """跟随重定向后的终址; 探测失败时返回 ``url``.

        调用方据此判定上游是否改派了别的资源 (如占位图).
        """
        _, final_url = await self._probe(url, use_proxy=use_proxy)
        return final_url

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        use_proxy: bool = True,
        chunked_threshold: int = 2 * 1024**2,
        chunk_size: int = 1 * 1024**2,
        download_concurrency: int = 10,
    ) -> bool:
        """大于 chunked_threshold 时分块并发下载. 失败返回 False."""
        file_size, _ = await self._probe(url, use_proxy=use_proxy)

        if file_size and file_size > chunked_threshold:
            return await self._download_chunked(
                url, dest, file_size, use_proxy=use_proxy, chunk_size=chunk_size, concurrency=download_concurrency
            )

        try:
            content = await self.get_bytes(url, use_proxy=use_proxy)
        except RequestError as e:
            logger.error("download failed", url=url, error=e.message)
            return False

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest, "wb") as f:
                await f.write(content)
            return True
        except Exception as e:
            logger.error("file write failed", path=str(dest), error=str(e))
            return False

    async def _download_chunked(
        self,
        url: str,
        dest: Path,
        file_size: int,
        *,
        use_proxy: bool = True,
        chunk_size: int = 1 * 1024**2,
        concurrency: int = 10,
    ) -> bool:
        parts = [(s, min(s + chunk_size - 1, file_size - 1)) for s in range(0, file_size, chunk_size)]

        logger.info("chunked download started", url=url, chunks=len(parts), size=file_size)

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(dest, "wb") as f:
                await f.truncate(file_size)
        except Exception as e:
            logger.error("file create failed", path=str(dest), error=str(e))
            return False

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_chunk(start: int, end: int) -> str:
            async with semaphore:
                try:
                    resp = await self.request(
                        "GET", url, headers={"Range": f"bytes={start}-{end}"}, use_proxy=use_proxy
                    )
                except RequestError as e:
                    return e.message
                async with aiofiles.open(dest, "rb+") as f:
                    await f.seek(start)
                    await f.write(resp.content)
                return ""

        results = await asyncio.gather(*[_fetch_chunk(s, e) for s, e in parts], return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("chunk download failed", chunk=i, url=url, error=str(result))
                return False
            if result:  # 非空错误字符串
                logger.error("chunk download failed", chunk=i, url=url, error=result)
                return False

        logger.info("chunked download complete", url=url)
        return True

    async def close(self) -> None:
        try:
            await self._session.close()
        except Exception as e:
            logger.debug("session close error (ignored)", error=str(e))


class BrowserClient:
    """延迟初始化: 浏览器仅在首次使用时启动."""

    def __init__(self, *, headless: bool = True, default_timeout: float = 30000):
        self._headless = headless
        self._default_timeout = default_timeout
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        async with self._lock:
            if self._browser is not None:
                return
            from patchright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                channel="chrome",
                headless=self._headless if os.getenv("AMANE_SHOW_BROWSER") is None else False,
                args=["--disable-blink-features=AutomationControlled"],
            )

    async def get_page(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> tuple[str | None, str]:
        """成功返回 ``(html, "")``, 失败返回 ``(None, 错误信息)``."""
        effective_timeout = timeout if timeout is not None else self._default_timeout
        try:
            await self._ensure_browser()
            assert self._browser is not None  # _ensure_browser 已保证
            page = await self._browser.new_page()
            try:
                await page.goto(url, timeout=effective_timeout, wait_until="domcontentloaded")
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=effective_timeout)
                content = await page.content()
                return content, ""
            finally:
                await page.close()
        except Exception as e:
            logger.error("browser page fetch failed", url=url, error=str(e))
            return None, str(e)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
