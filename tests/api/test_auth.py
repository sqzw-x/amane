"""API token 鉴权测试 (信任模型见 docs/dev/config.md).

默认 conftest 关闭 token (AMANE_TOKEN=off); 本文件自建 app 开启 token.
"""

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import tomli_w
from httpx2 import ASGITransport, AsyncClient

from amane.api.app import create_app
from amane.api.middleware import API_TOKEN_COOKIE
from amane.api.routes import API_PREFIX
from amane.config.token import resolve_api_token
from tests.api.conftest import hot_for_tests
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

TOKEN = "test-token-value"


def make_token_app(tmp_path: Path, token_env: str | None) -> FastAPI:
    os.environ["AMANE_DATA_DIR"] = str(tmp_path / "data")
    os.environ["AMANE_SAFE_DIRS"] = str(tmp_path / "files")
    os.environ["AMANE_LOG_DIR"] = str(tmp_path / "logs")
    if token_env is None:
        os.environ.pop("AMANE_TOKEN", None)
    else:
        os.environ["AMANE_TOKEN"] = token_env
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    copy_schema(tmp_path / "data" / "amane.db")
    (tmp_path / "data" / "config.toml").write_text(
        tomli_w.dumps(hot_for_tests().model_dump(mode="json", exclude_none=True))
    )
    return create_app()


@pytest_asyncio.fixture
async def token_client(tmp_path: Path):
    app = make_token_app(tmp_path, TOKEN)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
            yield client, app
    finally:
        await ctx.__aexit__(None, None, None)


class TestTokenAuth:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_token_rejected(self, token_client):
        client, _ = token_client
        resp = await client.get("libraries")
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wrong_token_rejected(self, token_client):
        client, _ = token_client
        resp = await client.get("libraries", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_token_accepted(self, token_client):
        client, _ = token_client
        resp = await client.get("libraries", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cookie_set_on_header_auth(self, token_client):
        """Bearer 认证成功 → 下发 HttpOnly SameSite=Lax cookie (子资源用)."""
        client, _ = token_client
        resp = await client.get("libraries", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert f"amane_token={TOKEN}" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/api" in set_cookie

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cookie_auth_accepted(self, token_client):
        """无 header, 仅凭 cookie (模拟 <img> 子资源) 通过."""
        client, _ = token_client
        resp = await client.get("libraries", headers={"Cookie": f"amane_token={TOKEN}"})
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cookie_lifecycle(self, token_client):
        """真实流程: header 认证落下 cookie, 后续裸请求 (无 header) 凭 cookie 通过."""
        client, _ = token_client
        resp = await client.get("libraries", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        resp = await client.get("libraries")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="function")
    async def test_wrong_cookie_rejected(self, token_client):
        client, _ = token_client
        resp = await client.get("libraries", headers={"Cookie": "amane_token=wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cookie_not_set_on_unauthorized(self, token_client):
        client, _ = token_client
        resp = await client.get("libraries")
        assert resp.status_code == 401
        assert "set-cookie" not in resp.headers

    @pytest.mark.asyncio(loop_scope="function")
    async def test_health_exempt(self, token_client):
        client, _ = token_client
        resp = await client.get("health")
        assert resp.status_code == 200

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_requires_token(self, token_client):
        client, _ = token_client
        resp = await client.get("system/release")
        assert resp.status_code == 401

    def test_ws_token_enforced(self):
        """WS 校验用最小 app 测 (TestClient 与完整中间件链的 WS 交互有已知
        怪癖, 现有 test_ws.py 同法避开; 生产路径由 uvicorn 实测)."""
        from fastapi import FastAPI
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from amane.api.routes.ws import router
        from amane.events import EventBus

        app = FastAPI()
        app.include_router(router)
        app.state.runtime = type("R", (), {"api_token": "tok-123", "event_bus": EventBus()})()
        with TestClient(app) as client:
            # 无 cookie → 1008 拒绝
            with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("ws"):
                pass
            assert exc_info.value.code == 1008
            # 正确 cookie → 建立 (握手即 HTTP, 同源自动携带)
            with client.websocket_connect("ws", headers={"Cookie": f"{API_TOKEN_COOKIE}=tok-123"}):
                pass

    def test_ws_token_off_passes(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from amane.api.routes.ws import router
        from amane.events import EventBus

        app = FastAPI()
        app.include_router(router)
        app.state.runtime = type("R", (), {"api_token": None, "event_bus": EventBus()})()
        with TestClient(app) as client, client.websocket_connect("ws"):
            pass

    @pytest.mark.asyncio(loop_scope="function")
    async def test_off_mode_disables_auth(self, tmp_path: Path):
        app = make_token_app(tmp_path, "off")
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
                resp = await client.get("libraries")
                assert resp.status_code == 200
        finally:
            await ctx.__aexit__(None, None, None)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_auto_mode_persists_token(self, tmp_path: Path):
        app = make_token_app(tmp_path, None)
        token = resolve_api_token(None, tmp_path / "data")
        assert token is not None
        token_file = tmp_path / "data" / "token"
        assert token_file.is_file()
        # 再次解析 (模拟重启) 返回同一 token
        assert resolve_api_token(None, tmp_path / "data") == token
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
                resp = await client.get("libraries", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 200
                # 模拟新会话 (无 cookie jar): 裸请求仍须 401
                client.cookies.clear()
                resp = await client.get("libraries")
                assert resp.status_code == 401
        finally:
            await ctx.__aexit__(None, None, None)
