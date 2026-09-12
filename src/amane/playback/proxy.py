"""Reverse-proxy an upstream media URL. Secrets stay on this hop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Literal

import httpx2 as httpx
import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ..plugins.api import UpstreamPlaybackTarget

logger = structlog.get_logger()

ProxyAllow = Literal["media", "hls_part", "subtitle"]
PLAYLIST_MAX_BYTES = 2 * 1024 * 1024
HLS_PART_CACHE_CONTROL = "private, max-age=31536000, immutable"
HLS_TEXT_CACHE_CONTROL = "private, no-cache"
_HLS_TEXT_TYPES = frozenset({"text/plain", "text/vtt"})
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
    """清单内分片允许的 Content-Type.

    ``text/vtt`` 是清单内字幕分片的实际类型, ``text/plain`` 是文本型 AES 密钥的常见默认
    类型. 两者与 ``X-Content-Type-Options: nosniff`` 一起使用, 不会被浏览器当作脚本执行.
    """
    lowered = content_type.casefold().split(";", 1)[0].strip()
    if is_playlist_type(lowered):
        return False
    if not lowered:
        return True
    if lowered.startswith(("video/", "audio/")):
        return True
    return lowered in {"application/octet-stream", "binary/octet-stream"} or lowered in _HLS_TEXT_TYPES


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

    ``Content-Length`` 描述的是上游编码后的正文字节数; 正文经 httpx 解码后长度会改变, 因此
    上游声明了非 identity 的 ``Content-Encoding`` 时不允许再透传该值, 否则浏览器按错误的
    长度截断或挂起.
    """
    encoding = headers.get("content-encoding", "").casefold().strip()
    encoded = encoding not in {"", "identity"}
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP:
            continue
        if encoded and lower == "content-length":
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


class StreamClient:
    """Long-lived streaming HTTP client. Not the scrape WebClient."""

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
            follow_redirects=False,
            proxy=proxy,
            headers={"Accept-Encoding": "identity"},
        )
        self._gate = UpstreamGate()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_bytes(
        self,
        *,
        source_id: str,
        target: UpstreamPlaybackTarget,
        max_bytes: int = PLAYLIST_MAX_BYTES,
    ) -> bytes:
        url = _upstream_url(target.url)
        outbound: dict[str, str] = {**target.headers, "Accept-Encoding": "identity"}
        await self._gate.acquire(source_id)
        try:
            upstream_req = self._client.build_request("GET", url, headers=outbound)
            response = await self._client.send(upstream_req, stream=True)
        except httpx.RequestError as exc:
            self._gate.release(source_id)
            logger.warning("playback upstream request failed", source=source_id, error=str(exc))
            raise HTTPException(status_code=502, detail="上游不可达") from exc
        except BaseException:
            # 归还出口额度: 非 RequestError 的异常 (例如客户端已被 rebuild 关闭) 不能漏掉额度,
            # 否则额度随请求次数单调减少, 最终整个进程的播放固定返回 503.
            self._gate.release(source_id)
            raise
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
        except HTTPException:
            raise
        finally:
            await response.aclose()
            self._gate.release(source_id)

    async def proxy(
        self,
        request: Request,
        *,
        source_id: str,
        target: UpstreamPlaybackTarget,
        allow: ProxyAllow = "media",
        rewrite_playlist: Callable[[str], str] | None = None,
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
        await self._gate.acquire(source_id)
        try:
            upstream_req = self._client.build_request(method, url, headers=outbound)
            response = await self._client.send(upstream_req, stream=True)
        except httpx.RequestError as exc:
            self._gate.release(source_id)
            logger.warning("playback upstream request failed", source=source_id, error=str(exc))
            raise HTTPException(status_code=502, detail="上游不可达") from exc
        except BaseException:
            # 同 fetch_bytes: 任何异常都必须归还出口额度.
            self._gate.release(source_id)
            raise

        secrets = _secret_keys(target)
        try:
            if response.status_code == 304:
                filtered = _safe_headers(response.headers, secrets)
                for key in list(filtered):
                    if key.casefold() == "cache-control" and "immutable" in filtered[key].casefold():
                        filtered[key] = "private, no-cache"
                await response.aclose()
                self._gate.release(source_id)
                return Response(status_code=304, headers=filtered)
            if response.status_code == 416:
                # 客户端 Range 不可满足属于请求本身的问题, 与上游故障区分, 原样返回状态码.
                filtered = _safe_headers(response.headers, secrets)
                await response.aclose()
                self._gate.release(source_id)
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
                await response.aclose()
                self._gate.release(source_id)
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
        except HTTPException:
            await response.aclose()
            self._gate.release(source_id)
            raise

        filtered = _safe_headers(response.headers, secrets)
        if allow == "hls_part":
            filtered["Cache-Control"] = hls_part_cache_control(response.headers.get("content-type", ""))

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await response.aclose()
                self._gate.release(source_id)

        if method == "HEAD":
            await response.aclose()
            self._gate.release(source_id)
            return Response(status_code=response.status_code, headers=filtered)

        return StreamingResponse(
            body(),
            status_code=response.status_code,
            headers=filtered,
            media_type=filtered.get("content-type"),
        )
