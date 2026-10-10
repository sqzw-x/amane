"""curl_cffi TLS 指纹模拟 + 限速 + 重试; 爬虫 / 图片 / Emby 等对外 HTTP 统一经此模块.

指纹按 host 选用, 被站点拦下时轮换; 契约见 ``WebClient.request``.
"""

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any

import aiofiles
import httpx2 as httpx
import structlog
from aiolimiter import AsyncLimiter
from curl_cffi import CurlError
from curl_cffi.requests import AsyncSession, BrowserTypeLiteral, Response

from .errors import FailureKind, RequestError, RequestFailure
from .recording import get_bound_http_recorder, skip_body_recording

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from curl_cffi.requests.session import HttpMethod

    from ..config import SiteConfig

logger = structlog.get_logger()


_IMPERSONATE_OPTIONS: tuple[BrowserTypeLiteral, ...] = (
    "chrome123",
    "chrome124",
    "chrome131",
    "chrome136",
    "firefox133",
    "firefox135",
)
"""进程内可用的指纹; 轮换按此顺序取下一个."""

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 503, 504})

_IMPERSONATE_REJECTED_STATUSES = frozenset({403, 406})
"""判定「站点拦下此指纹」的状态码: 站点按 TLS 指纹放行时给出的典型响应;
404 表示资源不存在, 换指纹无意义."""

_MAX_IMPERSONATE_ROTATIONS = 3
"""单次请求最多轮换的指纹数. 与重试预算分开计, 因此被拦时单次调用最多发出 4 次请求."""


class _FingerprintPicker:
    """按 host 选 TLS 指纹: 换过的指纹对该 host 保持, 后续请求不再试探原指纹.

    host 比来源更精确 (同一来源的 HTML 与图片可能落在不同 host), 比 URL 更稳定 (图片 URL 各不相同).
    """

    def __init__(self, options: tuple[BrowserTypeLiteral, ...]) -> None:
        self._options = options
        self._default: BrowserTypeLiteral = random.choice(options)
        self._chosen: dict[str, BrowserTypeLiteral] = {}
        self._rejected: dict[str, set[BrowserTypeLiteral]] = {}

    @property
    def default(self) -> BrowserTypeLiteral:
        return self._default

    def select(self, host: str) -> BrowserTypeLiteral:
        return self._chosen.get(host, self._default)

    def rotate(self, host: str) -> BrowserTypeLiteral | None:
        """把该 host 当前用的指纹记为拒绝项, 返回下一个未试过的; 全部试过返回 None."""
        rejected = self._rejected.setdefault(host, set())
        rejected.add(self.select(host))
        for option in self._options:
            if option not in rejected:
                self._chosen[host] = option
                return option
        return None


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


def site_proxy_overrides(
    site_configs: Mapping[str, SiteConfig],
    site_urls: Mapping[str, list[str]],
) -> dict[str, str | None]:
    """按 host 汇总站点级代理: 值 ``None`` 表示该 host 直连 (站点关闭了代理).

    host 取自来源 profile 的 URL 与配置的 ``base_url``, 与 ``RateLimiters.from_config`` 是同一份集合;
    未出现在结果里的 host 使用全局 ``network.proxy``. 多个站点共享 host 时按遍历顺序最后一次写入生效.
    """
    overrides: dict[str, str | None] = {}
    for site, cfg in site_configs.items():
        if cfg.use_proxy and cfg.proxy is None:
            continue
        for url in (*site_urls.get(str(site), []), cfg.base_url):
            host = httpx.URL(url).host if url else None
            if host:
                overrides[host] = cfg.proxy if cfg.use_proxy else None
    return overrides


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
    """出站 HTTP 通道. 代理按 host 解析 (站点级覆盖全局), 指纹按 host 选用, 被拦后换下一个."""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        proxy_overrides: Mapping[str, str | None] | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_clients: int = 50,
        limiters: RateLimiters,
        same_origin_referer_hosts: frozenset[str] = frozenset(),
    ):
        self._proxy = proxy
        self._proxy_overrides = dict(proxy_overrides or {})
        self._timeout = timeout
        self._max_retries = max_retries
        self._limiters = limiters
        self._same_origin_referer_hosts = same_origin_referer_hosts
        self._fingerprints = _FingerprintPicker(_IMPERSONATE_OPTIONS)
        self._session = AsyncSession(
            max_clients=max_clients,
            verify=False,
            max_redirects=20,
            timeout=timeout,
            impersonate=self._fingerprints.default,
        )
        # 日志给出进程默认指纹, 便于判断某站点是否按 TLS 指纹放行.
        logger.info("web client created", impersonate=self._fingerprints.default)

    async def acquire(self, url: str) -> None:
        """按 host 取得限速许可. 供不经 ``request`` 的通道 (浏览器渲染 / solver) 复用同一限速."""
        await self._limiters.get(httpx.URL(url).host).acquire()

    def _proxy_for(self, host: str | None) -> str | None:
        """该 host 实际使用的代理: 站点级设定优先, 含显式直连 (值为 ``None``)."""
        if host in self._proxy_overrides:
            return self._proxy_overrides[host]
        return self._proxy

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

        ``max_retries`` 是首次请求之外的**重试次数** (``2`` → 最多发 3 次请求); ``max_attempts`` 是
        **总尝试次数** 上限, 供一次性的探测向下覆盖 (探测传 1 表示只发一次). 两者都至少发一次请求,
        配置 0 表示不重试而不是一次都不发.

        ``use_proxy=False`` 强制直连 (本机 solver 服务用), 否则按 host 应用站点级代理, 未配置时用全局
        ``network.proxy``. 代理地址由 ``_proxy_for`` 决定, 同一 host 的连续请求走同一代理.

        403/406 另按「站点拦下此指纹」处理: 换下一个未试过的指纹重发, 换过的指纹对该 host 保持.
        轮换不占重试次数, 单次请求最多换 ``_MAX_IMPERSONATE_ROTATIONS`` 次; 该 host 的指纹全部被拒后
        不再轮换, 按原状态失败.
        """
        host = httpx.URL(url).host
        headers = _with_same_origin_referer(host, headers, self._same_origin_referer_hosts)
        await self._limiters.get(host).acquire()

        key = host or url
        total_attempts = 1 + self._max_retries
        attempts = max(1, total_attempts if max_attempts is None else min(max_attempts, total_attempts))
        t0 = time.monotonic()
        failure: RequestFailure | None = None
        resp: Response | None = None
        sent = 0
        retries = 0
        rotations = 0
        while True:
            sent += 1
            resp = None
            impersonate = self._fingerprints.select(key)
            should_retry = False
            try:
                resp = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    cookies=cookies,
                    data=data,
                    json=json,
                    proxy=self._proxy_for(host) if use_proxy else None,
                    timeout=timeout or self._timeout,
                    allow_redirects=allow_redirects,
                    impersonate=impersonate,
                )
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
            except TimeoutError:
                failure = RequestFailure(kind=FailureKind.TIMEOUT, message="timeout")
                should_retry = True
            except Exception as e:
                failure = RequestFailure(kind=FailureKind.UNEXPECTED, message=f"unexpected: {type(e).__name__}: {e}")

            if resp is not None and resp.status_code in _IMPERSONATE_REJECTED_STATUSES:
                # 先记录拒绝项并选出下一个: 预算用尽时它留给该 host 的下一次请求, 不重复试探同一个指纹.
                rotated = self._fingerprints.rotate(key)
                if rotated is None:
                    logger.warning("host rejected every fingerprint", url=url, status=resp.status_code)
                elif rotations < _MAX_IMPERSONATE_ROTATIONS:
                    rotations += 1
                    logger.warning(
                        "impersonate rotated",
                        url=url,
                        status=resp.status_code,
                        rejected=impersonate,
                        impersonate=rotated,
                    )
                    # 轮换重发是同一 host 的额外请求, 与首次请求一样先取限速许可: 严格平滑桶不允许突发.
                    await self._limiters.get(host).acquire()
                    continue

            if not should_retry or retries >= attempts - 1:
                break
            wait = retries * 3 + 2 + random.uniform(-1, 1)
            retries += 1
            logger.warning(
                "request retry",
                method=method,
                url=url,
                attempt=retries,
                max_retries=attempts,
                error=failure.message if failure else None,
                retry_in=wait,
            )
            await asyncio.sleep(wait)

        log_failed = logger.debug if get_bound_http_recorder() is not None else logger.error
        log_failed(
            "request failed",
            method=method,
            url=url,
            error=failure.message if failure else None,
            attempts=sent,
            duration_s=round(time.monotonic() - t0, 2),
        )
        self._record_exchange(method, url, resp=resp, error=failure.message if failure else None, t0=t0, attempts=sent)
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
        with skip_body_recording():
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
        """大于 chunked_threshold 时分块并发下载. 失败返回 False, 并按状态与原因记日志."""
        file_size, _ = await self._probe(url, use_proxy=use_proxy)

        if file_size and file_size > chunked_threshold:
            return await self._download_chunked(
                url, dest, file_size, use_proxy=use_proxy, chunk_size=chunk_size, concurrency=download_concurrency
            )

        try:
            content = await self.get_bytes(url, use_proxy=use_proxy)
        except RequestError as e:
            logger.error(
                "download failed",
                url=url,
                status=e.failure.status if e.failure else None,
                reason=e.reason,
                error=e.message,
            )
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

        async def _fetch_chunk(start: int, end: int) -> RequestFailure | None:
            async with semaphore:
                try:
                    resp = await self.request(
                        "GET", url, headers={"Range": f"bytes={start}-{end}"}, use_proxy=use_proxy
                    )
                except RequestError as e:
                    return e.failure or RequestFailure(kind=FailureKind.UNEXPECTED, message=e.message)
                async with aiofiles.open(dest, "rb+") as f:
                    await f.seek(start)
                    await f.write(resp.content)
                return None

        results = await asyncio.gather(*[_fetch_chunk(s, e) for s, e in parts], return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("chunk download failed", chunk=i, url=url, error=str(result))
                return False
            if result is not None:
                logger.error("chunk download failed", chunk=i, url=url, status=result.status, error=result.message)
                return False

        logger.info("chunked download complete", url=url)
        return True

    async def close(self) -> None:
        try:
            await self._session.close()
        except Exception as e:
            logger.debug("session close error (ignored)", error=str(e))
