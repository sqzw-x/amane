"""响应压缩: 压什么、不压什么."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx2 import ASGITransport, AsyncClient
from starlette.responses import Response, StreamingResponse

from amane.config import HotSettings
from tests.api.conftest import make_app
from tests.helpers import write_spa_dist

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

BIG_JSON = {"blob": "x" * 40_000}
"""远超 GZIP_MINIMUM_SIZE, 且压缩比很高."""

SMALL_JSON = {"ok": True}
"""远小于 GZIP_MINIMUM_SIZE; 见 test_small_json_is_also_compressed 的说明."""

IMAGE = bytes(range(256)) * 160
"""不可压缩字节冒充图片: 长度过线, 但按 content-type 必须原样放行."""

CHUNK = b"chunk" * 200
"""流式响应的一片正文; 两片合计同样过线."""

ASSET = b"// " + b"a" * 200_000
"""模拟 Vite 产物里的入口 chunk."""


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> FastAPI:
    """真实中间件栈 + 几种代表性响应, 用于对照压缩行为."""
    root = tmp_path_factory.mktemp("compression")
    application = make_app(HotSettings(), root / "data", root / "logs", root / "files")

    @application.get("/api/compression/big.json")
    async def big_json() -> dict[str, str]:
        return BIG_JSON

    @application.get("/api/compression/small.json")
    async def small_json() -> dict[str, bool]:
        return SMALL_JSON

    @application.get("/api/compression/image.png")
    async def image() -> Response:
        return Response(IMAGE, media_type="image/png")

    @application.get("/api/compression/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield CHUNK
            yield CHUNK

        return StreamingResponse(chunks(), media_type="application/json")

    @application.get("/api/compression/sse")
    async def sse() -> StreamingResponse:
        async def events() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return application


@pytest.fixture(scope="module")
def spa_app(tmp_path_factory: pytest.TempPathFactory) -> FastAPI:
    """SPA 已挂载的实例: 校验 index.html 与 /assets 下的产物同样被压缩."""
    root = tmp_path_factory.mktemp("compression-spa")
    dist = write_spa_dist(root / "dist", asset_bytes=ASSET)
    return make_app(HotSettings(), root / "data", root / "logs", root / "files", web_dist=dist)


@pytest_asyncio.fixture(loop_scope="module")
async def raw_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """请求头带 gzip 的客户端. httpx 的 ASGI transport 会自行解压正文, 因此断言的是编码头与解压结果."""
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"accept-encoding": "gzip"}) as client:
        yield client
    await ctx.__aexit__(None, None, None)


@pytest_asyncio.fixture(loop_scope="module")
async def spa_client(spa_app: FastAPI) -> AsyncIterator[AsyncClient]:
    ctx = spa_app.router.lifespan_context(spa_app)
    await ctx.__aenter__()
    transport = ASGITransport(app=spa_app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"accept-encoding": "gzip"}) as client:
        yield client
    await ctx.__aexit__(None, None, None)


class TestCompression:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_big_json_is_compressed(self, raw_client: AsyncClient):
        response = await raw_client.get("/api/compression/big.json")
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.json() == BIG_JSON

    @pytest.mark.asyncio(loop_scope="module")
    async def test_small_json_is_also_compressed(self, raw_client: AsyncClient):
        """小响应也会被压.

        内层 `_SPAFallbackMiddleware` 是 BaseHTTPMiddleware, 正文以分块消息交出, GZip 看不到完整
        长度, Starlette 的 minimum_size 快路径不会命中 —— 这与 GZip 注册在哪一层无关. 代价实测约
        4 微秒 (几十字节的正文), 换来的是不必自己写一层缓冲正文的中间件.
        """
        response = await raw_client.get("/api/compression/small.json")
        assert response.headers["content-encoding"] == "gzip"
        assert response.json() == SMALL_JSON

    @pytest.mark.asyncio(loop_scope="module")
    async def test_already_compressed_media_is_left_alone(self, raw_client: AsyncClient):
        """图片 / 视频 / SSE 压缩只会白烧 CPU: 上游已经压过, 或带 Range 与流式语义."""
        response = await raw_client.get("/api/compression/image.png")
        assert "content-encoding" not in response.headers
        assert response.content == IMAGE

    @pytest.mark.asyncio(loop_scope="module")
    async def test_stream_is_compressed_chunk_by_chunk(self, raw_client: AsyncClient):
        """分块响应没有 Content-Length, 走的是"边流边压": 正文与长度都被改写, 但字节仍完整."""
        response = await raw_client.get("/api/compression/stream")
        assert response.headers["content-encoding"] == "gzip"
        assert "content-length" not in response.headers
        assert response.content == CHUNK * 2

    @pytest.mark.asyncio(loop_scope="module")
    async def test_event_stream_is_left_alone(self, raw_client: AsyncClient):
        """SSE 必须逐条送达: 压缩会把它缓冲成整段, 前端就看不到增量输出."""
        response = await raw_client.get("/api/compression/sse")
        assert "content-encoding" not in response.headers
        assert response.text == "data: hello\n\n"

    @pytest.mark.asyncio(loop_scope="module")
    async def test_client_without_gzip_gets_identity(self, app: FastAPI):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers={"accept-encoding": "identity"}
        ) as client:
            response = await client.get("/api/compression/big.json")
        assert "content-encoding" not in response.headers
        assert response.json() == BIG_JSON


class TestSpaCompression:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_entry_chunk_is_compressed(self, spa_client: AsyncClient):
        response = await spa_client.get("/assets/index-test.js")
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert response.content == ASSET

    @pytest.mark.asyncio(loop_scope="module")
    async def test_spa_html_fallback_is_compressed(self, spa_client: AsyncClient):
        response = await spa_client.get("/some/deep/route")
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert b"<html" in response.content
