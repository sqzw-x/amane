"""浏览器信任边界: cookie 写请求的来源检查与 token 关闭时的 CORS; 契约见 docs/dev/api.md."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from amane.api.middleware import API_TOKEN_COOKIE
from amane.api.routes import API_PREFIX
from amane.config import HotSettings
from tests.api.conftest import make_app, ws_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

TOKEN = "test-token-value"
COOKIE = {"Cookie": f"{API_TOKEN_COOKIE}={TOKEN}"}
WRITE_ENDPOINT = "tasks/worker/pause"


@asynccontextmanager
async def _running_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
            yield client
    finally:
        await ctx.__aexit__(None, None, None)


def _secure_app(tmp_path: Path, *, trusted_origins: str = "") -> FastAPI:
    """token 开启 (cookie 认证生效) 的应用; trusted_origins 为空时走开发默认."""
    return make_app(
        HotSettings(),
        tmp_path / "data",
        tmp_path / "logs",
        tmp_path / "files",
        token=TOKEN,
        trusted_origins=trusted_origins,
    )


class TestCookieWriteSource:
    """cookie 认证的非安全方法按 Sec-Fetch-Site / Origin 判定来源."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_sec_fetch_site(self, tmp_path: Path):
        cases: list[tuple[str | None, int]] = [
            ("same-origin", 200),
            ("none", 200),
            ("same-site", 403),
            ("cross-site", 403),
            # 未知取值不放行
            ("cross-origin", 403),
        ]
        async with _running_client(_secure_app(tmp_path)) as client:
            for value, expected in cases:
                headers = {**COOKIE, "Sec-Fetch-Site": value}
                resp = await client.post(WRITE_ENDPOINT, headers=headers)
                assert resp.status_code == expected, f"Sec-Fetch-Site={value}"
                if expected == 403:
                    assert "来源" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_origin_fallback(self, tmp_path: Path):
        """缺 Sec-Fetch-Site 时: 无 Origin 放行 (脚本), 同源或白名单放行, 其余拒绝."""
        cases: list[tuple[str | None, int]] = [
            (None, 200),
            ("http://test", 200),
            ("http://localhost:5173", 200),
            ("http://127.0.0.1:5173", 200),
            ("http://localhost:8000", 403),
            ("http://evil.example", 403),
            # 不透明来源与非法值
            ("null", 403),
            ("not a url", 403),
        ]
        async with _running_client(_secure_app(tmp_path)) as client:
            for origin, expected in cases:
                headers = dict(COOKIE)
                if origin is not None:
                    headers["Origin"] = origin
                resp = await client.post(WRITE_ENDPOINT, headers=headers)
                assert resp.status_code == expected, f"Origin={origin!r}"
                if expected == 403:
                    assert "来源" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_configured_origins_replace_dev_default(self, tmp_path: Path):
        app = _secure_app(tmp_path, trusted_origins="http://ui.example:4000,https://admin.example")
        async with _running_client(app) as client:
            for origin, expected in [
                ("http://ui.example:4000", 200),
                ("https://admin.example", 200),
                ("http://localhost:5173", 403),
            ]:
                resp = await client.post(WRITE_ENDPOINT, headers={**COOKIE, "Origin": origin})
                assert resp.status_code == expected, f"Origin={origin!r}"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_bearer_and_safe_methods_not_checked(self, tmp_path: Path):
        async with _running_client(_secure_app(tmp_path)) as client:
            # Bearer 是脚本 / API 客户端凭证, 不受来源检查
            resp = await client.post(
                WRITE_ENDPOINT,
                headers={"Authorization": f"Bearer {TOKEN}", "Sec-Fetch-Site": "cross-site"},
            )
            assert resp.status_code == 200

            # 只读方法不做来源检查
            resp = await client.get("libraries", headers={**COOKIE, "Sec-Fetch-Site": "cross-site"})
            assert resp.status_code == 200

            # 无效 cookie 仍是鉴权失败, 不会被来源检查掩盖
            resp = await client.post(
                WRITE_ENDPOINT,
                headers={"Cookie": f"{API_TOKEN_COOKIE}=wrong", "Sec-Fetch-Site": "same-origin"},
            )
            assert resp.status_code == 401


class TestWebSocketSource:
    """WS 握手带 cookie 时同样拒绝同站 / 跨站来源."""

    def test_origin_checked(self):
        app = ws_app(token=TOKEN)
        with TestClient(app) as client:
            rejected = [
                {"Sec-Fetch-Site": "same-site"},
                {"Sec-Fetch-Site": "cross-site"},
                {"Origin": "http://evil.example"},
                {"Origin": "null"},
            ]
            for extra in rejected:
                with (
                    pytest.raises(WebSocketDisconnect) as exc_info,
                    client.websocket_connect("/ws", headers={**COOKIE, **extra}),
                ):
                    pass
                assert exc_info.value.code == 1008, extra

            accepted = [
                {"Sec-Fetch-Site": "same-origin"},
                {"Sec-Fetch-Site": "none"},
                {"Origin": "http://testserver"},
                {"Origin": "http://localhost:5173"},
                # 非浏览器握手没有来源头
                {},
            ]
            for extra in accepted:
                with client.websocket_connect("/ws", headers={**COOKIE, **extra}):
                    pass

    def test_configured_origins(self):
        app = ws_app(token=TOKEN, trusted_origins=["http://ui.example:4000"])
        with TestClient(app) as client:
            for extra in [{"Origin": "http://localhost:5173"}, {"Origin": "http://evil.example"}]:
                with (
                    pytest.raises(WebSocketDisconnect) as exc_info,
                    client.websocket_connect("/ws", headers={**COOKIE, **extra}),
                ):
                    pass
                assert exc_info.value.code == 1008, extra

            with client.websocket_connect("/ws", headers={**COOKIE, "Origin": "http://ui.example:4000"}):
                pass


class TestTokenOffCORS:
    """token 关闭时不放开跨源; 白名单需显式配置."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_cors_headers(self, tmp_path: Path):
        app = make_app(HotSettings(), tmp_path / "data", tmp_path / "logs", tmp_path / "files")
        async with _running_client(app) as client:
            resp = await client.get("libraries", headers={"Origin": "http://evil.example"})
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers

            preflight = await client.options(
                "libraries",
                headers={
                    "Origin": "http://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 400
            assert "access-control-allow-origin" not in preflight.headers

    @pytest.mark.asyncio(loop_scope="function")
    async def test_configured_origin_allowed(self, tmp_path: Path):
        app = make_app(
            HotSettings(),
            tmp_path / "data",
            tmp_path / "logs",
            tmp_path / "files",
            cors_origins="http://ui.example:8080",
        )
        async with _running_client(app) as client:
            resp = await client.get("libraries", headers={"Origin": "http://ui.example:8080"})
            assert resp.headers["access-control-allow-origin"] == "http://ui.example:8080"
            assert (
                "access-control-allow-origin"
                not in (await client.get("libraries", headers={"Origin": "http://evil.example"})).headers
            )

            preflight = await client.options(
                "libraries",
                headers={
                    "Origin": "http://ui.example:8080",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "http://ui.example:8080"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_token_on_keeps_open_cors(self, tmp_path: Path):
        app = make_app(HotSettings(), tmp_path / "data", tmp_path / "logs", tmp_path / "files", token=TOKEN)
        async with _running_client(app) as client:
            auth = {"Authorization": f"Bearer {TOKEN}"}
            resp = await client.get("libraries", headers={**auth, "Origin": "http://evil.example"})
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "*"

            preflight = await client.options(
                "libraries",
                headers={
                    "Origin": "http://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "*"
