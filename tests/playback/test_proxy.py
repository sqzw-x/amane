"""Upstream reverse-proxy constraints: Range, secrets, playlist, redirects, cancel."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx2 import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from amane.playback.proxy import (
    ProxyAllow,
    StreamClient,
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
        ("text/vtt", False),
        ("application/vnd.apple.mpegurl", False),
        ("application/json", False),
    ],
)
def test_allowed_hls_part(content_type: str, allowed: bool) -> None:
    assert is_allowed_hls_part(content_type) is allowed


@pytest.mark.parametrize(
    ("content_type", "allowed"),
    [
        ("text/vtt", True),
        ("text/plain", True),
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
