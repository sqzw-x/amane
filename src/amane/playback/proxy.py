"""Reverse-proxy an upstream media URL. Secrets stay on this hop."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
from typing import Literal

import httpx2 as httpx
import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from ..plugins.api import UpstreamPlaybackTarget

logger = structlog.get_logger()

ProxyAllow = Literal["media", "hls_part", "subtitle"]
PLAYLIST_MAX_BYTES = 2 * 1024 * 1024
HLS_PART_CACHE_CONTROL = "private, max-age=31536000, immutable"
HLS_TEXT_CACHE_CONTROL = "private, no-cache"
HLS_KEY_CACHE_CONTROL = "private, no-store"
_HLS_TEXT_TYPES = frozenset({"text/plain", "text/vtt"})
HLS_PART_MEDIA_TYPE = "application/octet-stream"
PLAYLIST_MEDIA_TYPE = "application/vnd.apple.mpegurl"
PLAYLIST_CACHE_CONTROL = "private, no-cache"

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "set-cookie",
        "set-cookie2",
    }
)
_PASSTHROUGH = frozenset(
    {
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
        "cache-control",
        "expires",
        "age",
    }
)
_PLAYLIST_MARKERS = ("mpegurl", "dash+xml", "x-mpegurl")

GLOBAL_CONCURRENCY = 16
PER_SOURCE_CONCURRENCY = 8
ACQUIRE_TIMEOUT_SECONDS = 2.0
PLAYBACK_READ_TIMEOUT_SECONDS = 30.0
DRAIN_TIMEOUT_SECONDS = 30.0
NOSNIFF = {"X-Content-Type-Options": "nosniff"}


def is_playlist_type(content_type: str) -> bool:
    lowered = content_type.casefold()
    return any(marker in lowered for marker in _PLAYLIST_MARKERS)


def is_allowed_media_type(content_type: str) -> bool:
    lowered = content_type.casefold().split(";", 1)[0].strip()
    if is_playlist_type(lowered):
        return False
    return lowered.startswith(("video/", "audio/"))


def is_allowed_hls_part(content_type: str) -> bool:
    """清单内分片允许的 Content-Type: 非播放列表即放行.

    上游 CDN 普遍伪装分片的扩展名与类型, 分片常被声明成 ``text/css``、``image/*`` 甚至
    ``text/html``; 按类型白名单拒绝会让整条流无法播放. 放行不代表按该类型输出: 转发时一律
    中和为 ``application/octet-stream``, 见 ``neutralized_hls_part_type``.
    """
    return not is_playlist_type(content_type)


def neutralized_hls_part_type(content_type: str) -> str:
    """分片转发给浏览器时使用的 Content-Type.

    ``text/vtt`` 是清单内字幕分片的语义类型, ``text/plain`` 是文本型 AES 密钥的常见默认
    类型, 两者保留原类型; 其余类型 (含上游伪装成的可执行 / 可渲染类型与空类型) 一律中和为
    ``application/octet-stream``. 与 ``X-Content-Type-Options: nosniff`` 一起, 上游即使返回
    可执行类型也不会被浏览器按该类型处理.
    """
    lowered = content_type.casefold().split(";", 1)[0].strip()
    return content_type if lowered in _HLS_TEXT_TYPES else HLS_PART_MEDIA_TYPE


def hls_part_cache_control(content_type: str) -> str:
    """清单内分片的缓存策略.

    密钥与字幕按 URI 复用但内容可能轮换, 因此文本类不做不可变缓存; 只有媒体分片与初始化段
    按 URI 长期不变, 才允许写入浏览器不可变缓存.
    """
    lowered = content_type.casefold().split(";", 1)[0].strip()
    return HLS_TEXT_CACHE_CONTROL if lowered in _HLS_TEXT_TYPES else HLS_PART_CACHE_CONTROL


def is_allowed_subtitle_type(content_type: str) -> bool:
    lowered = content_type.casefold().split(";", 1)[0].strip()
    if not lowered:
        return True
    return lowered == "text/vtt"


def _content_type_allowed(allow: ProxyAllow, content_type: str) -> bool:
    if allow == "media":
        return is_allowed_media_type(content_type)
    if allow == "hls_part":
        return is_allowed_hls_part(content_type)
    return is_allowed_subtitle_type(content_type)


def _is_multi_range(range_header: str) -> bool:
    spec = range_header.strip()
    if not spec.lower().startswith("bytes="):
        return False
    return "," in spec.split("=", 1)[1]


def _upstream_url(url: str) -> str:
    """校验并归一化上游 URL.

    ``httpx.InvalidURL`` 直接继承 ``Exception``, 不是 ``RequestError``; 畸形 URL 若不在入口
    拦下, 会绕过 ``except httpx.RequestError`` 的归还分支漏掉出口额度, 并被路由记成未处理
    异常返回 500. 只接受主机可代理的绝对 http(s) 地址.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=502, detail="上游地址无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise HTTPException(status_code=502, detail="上游地址无效")
    return str(parsed)


class UpstreamGate:
    def __init__(
        self,
        *,
        global_limit: int = GLOBAL_CONCURRENCY,
        per_source_limit: int = PER_SOURCE_CONCURRENCY,
    ) -> None:
        self._global = asyncio.Semaphore(global_limit)
        self._per_source: dict[str, asyncio.Semaphore] = {}
        self._per_source_limit = per_source_limit
        self._lock = asyncio.Lock()

    async def acquire(self, source_id: str) -> None:
        async with self._lock:
            source_sem = self._per_source.get(source_id)
            if source_sem is None:
                source_sem = asyncio.Semaphore(self._per_source_limit)
                self._per_source[source_id] = source_sem
        try:
            await asyncio.wait_for(self._global.acquire(), timeout=ACQUIRE_TIMEOUT_SECONDS)
        except TimeoutError:
            raise HTTPException(status_code=503, detail="播放出口繁忙") from None
        try:
            await asyncio.wait_for(source_sem.acquire(), timeout=ACQUIRE_TIMEOUT_SECONDS)
        except TimeoutError:
            self._global.release()
            raise HTTPException(status_code=503, detail="播放出口繁忙") from None

    def release(self, source_id: str) -> None:
        source_sem = self._per_source.get(source_id)
        if source_sem is not None:
            source_sem.release()
        self._global.release()


def _secret_keys(target: UpstreamPlaybackTarget) -> set[str]:
    return {key.casefold() for key in target.headers}


def _filter_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """透传播放需要的上游响应头, 丢弃 hop-by-hop 与 cookie.

    ``Content-Length`` 与 ``Content-Range`` 描述的是上游编码后的字节; 正文经 httpx 解码后
    长度与范围都会改变, 因此上游声明了非 identity 的 ``Content-Encoding`` 时不允许再透传.
    """
    encoding = headers.get("content-encoding", "").casefold().strip()
    encoded = encoding not in {"", "identity"}
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP:
            continue
        if encoded and lower in {"content-length", "content-range"}:
            continue
        if lower in _PASSTHROUGH:
            out[key] = value
    return out


def _safe_headers(headers: Mapping[str, str], secrets: set[str]) -> dict[str, str]:
    """过滤后的响应头, 并删除与上游请求头同名的项.

    上游回显请求头时会把主机发出的凭据交给浏览器, 因此按名字逐一比对并删除.
    """
    out = _filter_response_headers(headers)
    out.update(NOSNIFF)
    for key in [key for key in out if key.casefold() in secrets]:
        del out[key]
    return out


def _override_header(headers: dict[str, str], name: str, value: str) -> None:
    """按大小写无关替换响应头.

    ``dict`` 的键区分大小写, 直接赋值会与上游透传的同名头并存, 客户端只会读到先出现的那份.
    """
    for key in [key for key in headers if key.casefold() == name.casefold()]:
        del headers[key]
    headers[name] = value


def _length_range_consistent(headers: Mapping[str, str]) -> bool:
    length = headers.get("content-length")
    content_range = headers.get("content-range")
    if length is None or content_range is None:
        return True
    try:
        declared = int(length)
    except ValueError:
        return False
    # bytes start-end/total
    unit, _, rest = content_range.partition(" ")
    if unit.casefold() != "bytes" or "/" not in rest:
        return True
    span, _, _total = rest.partition("/")
    if "-" not in span:
        return True
    start_s, _, end_s = span.partition("-")
    try:
        start = int(start_s)
        end = int(end_s)
    except ValueError:
        return True
    return declared == (end - start + 1)


class _GateBody:
    """包装流式正文, 使未迭代就被关闭时同样释放上游请求.

    未启动过的异步生成器 ``aclose()`` 不执行自身的 ``finally``, 因此释放必须在包装层再做一次.
    """

    def __init__(self, chunks: AsyncGenerator[bytes], release: Callable[[], Awaitable[None]]) -> None:
        self._chunks = chunks
        self._release = release

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        return await self._chunks.__anext__()

    async def aclose(self) -> None:
        try:
            await self._chunks.aclose()
        finally:
            await self._release()


class _GateStreamingResponse(StreamingResponse):
    """在响应生命周期结束时兜底释放上游请求.

    ``body()`` 的 ``finally`` 只在生成器被迭代过时执行: 首块 ``send`` 抛 ``ClientDisconnect``
    (播放器切换码率或 seek 会主动中止在途分片) 时生成器从未启动, 只有 ``__call__`` 能观察到
    结束. 释放函数幂等, 两处都调用不会重复归还.
    """

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        release: Callable[[], Awaitable[None]],
        status_code: int,
        headers: Mapping[str, str],
        media_type: str | None,
    ) -> None:
        self._release = release
        super().__init__(content, status_code=status_code, headers=dict(headers), media_type=media_type)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._release()


class StreamClient:
    """Long-lived streaming HTTP client. Not the scrape WebClient."""

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=PLAYBACK_READ_TIMEOUT_SECONDS,
                write=30.0,
                pool=10.0,
            ),
            follow_redirects=False,
            proxy=proxy,
            headers={"Accept-Encoding": "identity"},
        )
        self._gate = UpstreamGate()
        self._in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def _enter(self) -> None:
        self._in_flight += 1
        self._idle.clear()

    def _exit(self) -> None:
        self._in_flight -= 1
        if self._in_flight <= 0:
            self._idle.set()

    async def aclose(self) -> None:
        """等在途请求结束后再关闭连接池.

        重建时立即关闭会掐断正在传输的分片; 卡死的上游由读超时与 ``DRAIN_TIMEOUT_SECONDS``
        兜底, 不让被替换的客户端无限期存活.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._idle.wait(), timeout=DRAIN_TIMEOUT_SECONDS)
        await self._client.aclose()

    async def _open_upstream(
        self,
        *,
        source_id: str,
        method: str,
        url: str,
        headers: Mapping[str, str],
    ) -> tuple[httpx.Response, Callable[[], Awaitable[None]]]:
        """发出上游请求, 同时返回一个幂等的一次性释放函数.

        释放函数负责关闭上游响应并归还出口额度, 入口段与 post-send 段都必须调用它:
        ``httpx.InvalidURL`` 不是 ``RequestError``, ``aread()`` 的 ``ReadTimeout`` 与清单改写
        的 ``SourceError`` 也不属于 ``HTTPException``; 只按单一异常类型释放会漏掉额度, 累积到
        全局上限后整个进程的播放固定返回 503.
        """
        await self._gate.acquire(source_id)
        self._enter()
        released = False
        response: httpx.Response | None = None

        async def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            if response is not None:
                await response.aclose()
            self._exit()
            self._gate.release(source_id)

        try:
            upstream_req = self._client.build_request(method, url, headers=dict(headers))
            response = await self._client.send(upstream_req, stream=True)
        except httpx.RequestError as exc:
            await release()
            logger.warning("playback upstream request failed", source=source_id, error=str(exc))
            raise HTTPException(status_code=502, detail="上游不可达") from exc
        except BaseException:
            await release()
            raise
        return response, release

    async def fetch_bytes(
        self,
        *,
        source_id: str,
        target: UpstreamPlaybackTarget,
        max_bytes: int = PLAYLIST_MAX_BYTES,
    ) -> bytes:
        url = _upstream_url(target.url)
        outbound: dict[str, str] = {**target.headers, "Accept-Encoding": "identity"}
        response, release = await self._open_upstream(
            source_id=source_id,
            method="GET",
            url=url,
            headers=outbound,
        )
        try:
            if 300 <= response.status_code < 400:
                raise HTTPException(status_code=502, detail="上游重定向被拒绝")
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="上游失败")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=502, detail="播放列表过大")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            await release()

    async def proxy(
        self,
        request: Request,
        *,
        source_id: str,
        target: UpstreamPlaybackTarget,
        allow: ProxyAllow = "media",
        rewrite_playlist: Callable[[str], str] | None = None,
        cache_control: str | None = None,
    ) -> Response:
        range_header = request.headers.get("range")
        if range_header is not None and _is_multi_range(range_header):
            raise HTTPException(status_code=400, detail="不支持多段 Range")

        outbound: dict[str, str] = {**target.headers}
        outbound["Accept-Encoding"] = "identity"
        if range_header is not None:
            outbound["Range"] = range_header
        if_range = request.headers.get("if-range")
        if if_range is not None:
            outbound["If-Range"] = if_range
        if_none_match = request.headers.get("if-none-match")
        if if_none_match is not None:
            outbound["If-None-Match"] = if_none_match

        method = "HEAD" if request.method == "HEAD" else "GET"
        url = _upstream_url(target.url)
        response, release = await self._open_upstream(
            source_id=source_id,
            method=method,
            url=url,
            headers=outbound,
        )

        secrets = _secret_keys(target)
        try:
            if response.status_code == 304:
                filtered = _safe_headers(response.headers, secrets)
                for key in list(filtered):
                    if key.casefold() == "cache-control" and "immutable" in filtered[key].casefold():
                        filtered[key] = "private, no-cache"
                await release()
                return Response(status_code=304, headers=filtered)
            if response.status_code == 416:
                # 客户端 Range 不可满足属于请求本身的问题, 与上游故障区分, 原样返回状态码.
                filtered = _safe_headers(response.headers, secrets)
                await release()
                return Response(status_code=416, headers=filtered)
            if 300 <= response.status_code < 400:
                raise HTTPException(status_code=502, detail="上游重定向被拒绝")
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="上游失败")
            content_type = response.headers.get("content-type", "")
            if is_playlist_type(content_type) and rewrite_playlist is not None and method == "GET":
                raw = await response.aread()
                if len(raw) > PLAYLIST_MAX_BYTES:
                    raise HTTPException(status_code=502, detail="播放列表过大")
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise HTTPException(status_code=502, detail="播放列表不是文本") from exc
                text = rewrite_playlist(decoded)
                await release()
                return Response(
                    content=text.encode("utf-8"),
                    media_type=PLAYLIST_MEDIA_TYPE,
                    headers={**NOSNIFF, "Cache-Control": PLAYLIST_CACHE_CONTROL},
                )
            if not _content_type_allowed(allow, content_type):
                logger.warning(
                    "playback upstream content-type rejected",
                    source=source_id,
                    content_type=content_type,
                )
                raise HTTPException(status_code=502, detail="上游不是可播放的媒体")
            if not _length_range_consistent({k.lower(): v for k, v in response.headers.items()}):
                raise HTTPException(status_code=502, detail="上游长度与 Range 不一致")
        except BaseException:
            # post-send 段: aread() 的 ReadTimeout 与清单改写的 SourceError 都不属于
            # HTTPException, 只捕 HTTPException 会漏掉出口额度.
            await release()
            raise

        filtered = _safe_headers(response.headers, secrets)
        # 分片的类型判定与缓存判定都依据上游声明的类型, 输出给浏览器的则是中和后的类型.
        # 密钥 URI 由 token 的 ``is_key`` 标记传入 ``cache_control`` (no-store); 其余文本类
        # 分片按 no-cache, 媒体分片与初始化段按不可变缓存.
        upstream_type = response.headers.get("content-type", "")
        if allow == "hls_part":
            _override_header(filtered, "content-type", neutralized_hls_part_type(upstream_type))
            _override_header(
                filtered,
                "cache-control",
                cache_control if cache_control is not None else hls_part_cache_control(upstream_type),
            )
        elif cache_control is not None:
            _override_header(filtered, "cache-control", cache_control)

        async def body() -> AsyncGenerator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await release()

        if method == "HEAD":
            await release()
            return Response(status_code=response.status_code, headers=filtered)

        return _GateStreamingResponse(
            _GateBody(body(), release),
            release=release,
            status_code=response.status_code,
            headers=filtered,
            media_type=filtered.get("content-type"),
        )
