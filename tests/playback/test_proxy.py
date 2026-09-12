"""Upstream reverse-proxy constraints: Range, secrets, playlist, redirects, cancel."""

import gzip
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx2 import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from amane.playback.proxy import (
    GLOBAL_CONCURRENCY,
    HLS_PART_CACHE_CONTROL,
    HLS_TEXT_CACHE_CONTROL,
    ProxyAllow,
    StreamClient,
    _filter_response_headers,
    hls_part_cache_control,
    is_allowed_hls_part,
    is_allowed_media_type,
    is_allowed_subtitle_type,
    is_playlist_type,
)
from amane.plugins.api import UpstreamPlaybackTarget


@pytest.mark.parametrize(
    ("content_type", "allowed"),
    [
        ("video/mp4", True),
        ("audio/mpeg", True),
        ("video/mp4; codecs=avc1", True),
        ("application/vnd.apple.mpegurl", False),
        ("application/dash+xml", False),
        ("application/json", False),
        ("", False),
        ("text/html", False),
    ],
)
def test_allowed_media_type(content_type: str, allowed: bool) -> None:
    assert is_allowed_media_type(content_type) is allowed
    if "mpegurl" in content_type or "dash+xml" in content_type:
        assert is_playlist_type(content_type) is True


@pytest.mark.parametrize(
    ("content_type", "allowed"),
    [
        ("video/mp2t", True),
        ("audio/aac", True),
        ("application/octet-stream", True),
        ("", True),
        ("text/html", False),
        ("text/vtt", True),
        ("text/plain", True),
        ("application/vnd.apple.mpegurl", False),
        ("application/json", False),
    ],
)
def test_allowed_hls_part(content_type: str, allowed: bool) -> None:
    assert is_allowed_hls_part(content_type) is allowed


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("video/mp2t", HLS_PART_CACHE_CONTROL),
        ("application/octet-stream", HLS_PART_CACHE_CONTROL),
        ("text/plain", HLS_TEXT_CACHE_CONTROL),
        ("text/vtt; charset=utf-8", HLS_TEXT_CACHE_CONTROL),
    ],
)
def test_hls_part_cache_control(content_type: str, expected: str) -> None:
    """密钥与字幕按 URI 复用但内容会轮换, 不允许写不可变缓存."""
    assert hls_part_cache_control(content_type) == expected


@pytest.mark.parametrize(
    ("content_type", "allowed"),
    [
        ("text/vtt", True),
        ("text/plain", False),
        ("", True),
        ("text/html", False),
        ("video/mp4", False),
    ],
)
def test_allowed_subtitle_type(content_type: str, allowed: bool) -> None:
    assert is_allowed_subtitle_type(content_type) is allowed


class _Upstream(BaseHTTPRequestHandler):
    captured: dict[str, str | None]
    body: bytes
    status: int
    media_type: str
    extra: dict[str, str]
    mismatch: bool
    slow: bool
    cancelled: Event

    def do_HEAD(self) -> None:
        self._send(with_body=False)

    def do_GET(self) -> None:
        self._send(with_body=True)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, *, with_body: bool) -> None:
        self.captured["range"] = self.headers.get("Range")
        self.captured["authorization"] = self.headers.get("Authorization")
        self.captured["accept_encoding"] = self.headers.get("Accept-Encoding")
        self.captured["if_none_match"] = self.headers.get("If-None-Match")
        if self.status == 304:
            self.send_response(304)
            self.send_header("ETag", '"seg"')
            for key, value in self.extra.items():
                self.send_header(key, value)
            self.end_headers()
            return
        if self.status >= 300 and self.status < 400:
            self.send_response(self.status)
            self.send_header("Location", "https://upstream.example/video")
            self.end_headers()
            return
        self.send_response(self.status)
        self.send_header("Content-Type", self.media_type)
        if self.slow:
            length = 65536 * 200
            self.send_header("Content-Length", str(length))
        elif self.mismatch:
            self.send_header("Content-Range", "bytes 0-20/100")
            self.send_header("Content-Length", "10")
        else:
            self.send_header("Content-Length", str(len(self.body)))
            if self.headers.get("Range"):
                self.send_header("Content-Range", f"bytes 0-{len(self.body) - 1}/{len(self.body)}")
        self.send_header("Set-Cookie", "session=secret")
        self.send_header("Authorization", "Bearer leaked")
        for key, value in self.extra.items():
            self.send_header(key, value)
        self.end_headers()
        if not with_body:
            return
        if self.slow:
            try:
                chunk = b"x" * 65536
                for _ in range(200):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except BrokenPipeError, ConnectionResetError, ConnectionAbortedError:
                self.cancelled.set()
            return
        self.wfile.write(self.body)


def _serve(**kwargs: Any) -> tuple[ThreadingHTTPServer, dict[str, str | None], Event]:
    captured: dict[str, str | None] = {}
    cancelled = Event()
    handler = type(
        "Handler",
        (_Upstream,),
        {
            "captured": captured,
            "cancelled": cancelled,
            "body": kwargs.get("body", b"abcdef" * 20),
            "status": kwargs.get("status", 200),
            "media_type": kwargs.get("media_type", "video/mp4"),
            "extra": kwargs.get("extra", {}),
            "mismatch": kwargs.get("mismatch", False),
            "slow": kwargs.get("slow", False),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, captured, cancelled


def _origin(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}/video"


def _app(url: str, headers: dict[str, str] | None = None, *, allow: ProxyAllow = "media") -> FastAPI:
    stream = StreamClient()
    target = UpstreamPlaybackTarget(url=url, headers=headers or {"Authorization": "Bearer secret"})

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await stream.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.api_route("/p", methods=["GET", "HEAD"])
    async def play(request: Request):
        return await stream.proxy(request, source_id="acme.play", target=target, allow=allow)

    return app


@pytest.mark.asyncio
async def test_proxy_forwards_range_and_strips_secrets() -> None:
    server, captured, _cancelled = _serve()
    try:
        app = _app(_origin(server))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/p", headers={"Range": "bytes=0-10"})
            assert resp.status_code == 200
            assert captured["range"] == "bytes=0-10"
            assert captured["authorization"] == "Bearer secret"
            assert captured["accept_encoding"] == "identity"
            assert "authorization" not in {k.casefold() for k in resp.headers}
            assert "set-cookie" not in {k.casefold() for k in resp.headers}

            multi = await client.get("/p", headers={"Range": "bytes=0-1,2-3"})
            assert multi.status_code == 400
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_proxy_rejects_redirect_playlist_and_mismatch() -> None:
    redirect, _c1, _ = _serve(status=302)
    playlist, _c2, _ = _serve(media_type="application/vnd.apple.mpegurl")
    mismatch, _c3, _ = _serve(mismatch=True)
    try:
        cases = [
            (redirect, 502),
            (playlist, 502),
            (mismatch, 502),
        ]
        for server, status in cases:
            app = _app(_origin(server))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/p")
                assert resp.status_code == status
    finally:
        redirect.shutdown()
        playlist.shutdown()
        mismatch.shutdown()


@pytest.mark.asyncio
async def test_proxy_cancels_upstream_on_disconnect() -> None:
    server, _captured, cancelled = _serve(slow=True)
    stream = StreamClient()
    try:
        target = UpstreamPlaybackTarget(
            url=_origin(server),
            headers={"Authorization": "Bearer secret"},
        )

        async def receive() -> dict[str, str]:
            return {"type": "http.disconnect"}

        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/p",
                "raw_path": b"/p",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 123),
                "server": ("test", 80),
            },
            receive,
        )
        response = await stream.proxy(request, source_id="acme.play", target=target)
        assert isinstance(response, StreamingResponse)
        async for _chunk in response.body_iterator:
            break
        assert cancelled.wait(timeout=2)
    finally:
        await stream.aclose()
        server.shutdown()


@pytest.mark.asyncio
async def test_proxy_passes_304_without_immutable() -> None:
    server, captured, _cancelled = _serve(
        status=304,
        extra={"Cache-Control": "private, max-age=31536000, immutable"},
    )
    try:
        app = _app(_origin(server), allow="hls_part")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/p", headers={"If-None-Match": '"seg"'})
            assert resp.status_code == 304
            assert captured["if_none_match"] == '"seg"'
            assert "immutable" not in resp.headers.get("cache-control", "").casefold()
            assert "authorization" not in {k.casefold() for k in resp.headers}
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_proxy_hls_part_rejects_4xx_and_html() -> None:
    missing, _c1, _ = _serve(status=404)
    html, _c2, _ = _serve(media_type="text/html", body=b"<html>x</html>")
    try:
        for server in (missing, html):
            app = _app(_origin(server), allow="hls_part")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/p")
                assert resp.status_code == 502
                assert "immutable" not in resp.headers.get("cache-control", "").casefold()
    finally:
        missing.shutdown()
        html.shutdown()


@pytest.mark.asyncio
async def test_proxy_passes_416_range_not_satisfiable() -> None:
    """客户端 Range 不可满足属于请求本身的问题, 不与上游故障一起折叠为 502."""
    server, _captured, _cancelled = _serve(status=416)
    try:
        app = _app(_origin(server), allow="hls_part")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/p", headers={"Range": "bytes=999-1000"})
            assert resp.status_code == 416
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    ("headers", "keeps_length"),
    [
        ({"content-type": "video/mp4", "content-length": "120"}, True),
        ({"content-type": "video/mp4", "content-length": "120", "content-encoding": "identity"}, True),
        ({"content-type": "video/mp4", "content-length": "40", "content-encoding": "gzip"}, False),
    ],
)
def test_filter_response_headers_drops_stale_length(headers: dict[str, str], keeps_length: bool) -> None:
    """正文经 httpx 解码后长度不等于上游声明值, 编码过的响应不得透传 Content-Length."""
    filtered = _filter_response_headers(headers)
    assert ("content-length" in filtered) is keeps_length


@pytest.mark.asyncio
async def test_proxy_decodes_encoded_upstream_without_stale_length() -> None:
    original = b"abcdef" * 20
    server, _captured, _cancelled = _serve(body=gzip.compress(original), extra={"Content-Encoding": "gzip"})
    try:
        app = _app(_origin(server))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/p")
            assert resp.status_code == 200
            assert resp.content == original
            assert "content-length" not in {key.casefold() for key in resp.headers}
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_upstream_failures_do_not_leak_gate_permits() -> None:
    """畸形上游 URL 必须归为上游失败, 且任何失败路径都归还出口额度.

    ``httpx.InvalidURL`` 直接继承 ``Exception`` 而不是 ``RequestError``. 额度一旦在异常路径
    漏掉, 累积到全局上限 (``GLOBAL_CONCURRENCY``) 之后所有播放请求固定 503. 这里先发起远多于
    上限的失败请求, 再确认同一客户端仍能完成正常请求.
    """
    server, _captured, _cancelled = _serve()
    stream = StreamClient()
    bad = UpstreamPlaybackTarget(url="http://upstream.example:bad/video")
    good = UpstreamPlaybackTarget(url=_origin(server), headers={"Authorization": "Bearer secret"})

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await stream.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/bad")
    async def bad_route(request: Request):
        return await stream.proxy(request, source_id="acme.play", target=bad)

    @app.get("/good")
    async def good_route(request: Request):
        return await stream.proxy(request, source_id="acme.play", target=good)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(GLOBAL_CONCURRENCY + 4):
                with pytest.raises(HTTPException) as failure:
                    await stream.fetch_bytes(source_id="acme.play", target=bad)
                assert failure.value.status_code == 502
                assert failure.value.detail == "上游地址无效"
                resp = await client.get("/bad")
                assert resp.status_code == 502
                assert resp.json()["detail"] == "上游地址无效"

            assert await stream.fetch_bytes(source_id="acme.play", target=good) == b"abcdef" * 20
            ok = await client.get("/good")
            assert ok.status_code == 200
    finally:
        server.shutdown()
