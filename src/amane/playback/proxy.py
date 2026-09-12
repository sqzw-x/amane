"""Reverse-proxy an upstream media URL. Secrets stay on this hop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import httpx2 as httpx
import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ..plugins.api import UpstreamPlaybackTarget

logger = structlog.get_logger()

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
PER_SOURCE_CONCURRENCY = 4


def is_playlist_type(content_type: str) -> bool:
    lowered = content_type.casefold()
    return any(marker in lowered for marker in _PLAYLIST_MARKERS)


def is_allowed_media_type(content_type: str) -> bool:
    lowered = content_type.casefold().split(";", 1)[0].strip()
    if is_playlist_type(lowered):
        return False
    return lowered.startswith(("video/", "audio/"))


def _is_multi_range(range_header: str) -> bool:
    spec = range_header.strip()
    if not spec.lower().startswith("bytes="):
        return False
    return "," in spec.split("=", 1)[1]


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
        if self._global.locked() or source_sem.locked():
            raise HTTPException(status_code=503, detail="播放出口繁忙")
        await self._global.acquire()
        try:
            await source_sem.acquire()
        except BaseException:
            self._global.release()
            raise

    def release(self, source_id: str) -> None:
        source_sem = self._per_source.get(source_id)
        if source_sem is not None:
            source_sem.release()
        self._global.release()


def _filter_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP:
            continue
        if lower in _PASSTHROUGH:
            out[key] = value
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

    async def proxy(
        self,
        request: Request,
        *,
        source_id: str,
        target: UpstreamPlaybackTarget,
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
        await self._gate.acquire(source_id)
        try:
            upstream_req = self._client.build_request(method, target.url, headers=outbound)
            response = await self._client.send(upstream_req, stream=True)
        except httpx.RequestError as exc:
            self._gate.release(source_id)
            logger.warning("playback upstream request failed", source=source_id, error=str(exc))
            raise HTTPException(status_code=502, detail="上游不可达") from exc

        try:
            if 300 <= response.status_code < 400:
                raise HTTPException(status_code=502, detail="上游重定向被拒绝")
            if response.status_code >= 500:
                raise HTTPException(status_code=502, detail="上游失败")
            content_type = response.headers.get("content-type", "")
            if not is_allowed_media_type(content_type):
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

        filtered = _filter_response_headers(response.headers)
        secret_keys = {key.casefold() for key in target.headers}
        for key in list(filtered):
            if key.casefold() in secret_keys:
                del filtered[key]

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
