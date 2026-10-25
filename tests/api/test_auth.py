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
from amane.config.token import resolve_api_token, resolve_image_token
from amane.media import ResourceStore
from tests.api.conftest import hot_for_tests
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

TOKEN = "test-token-value"
IMAGE_TOKEN = "test-image-token-value"


def make_token_app(tmp_path: Path, token_env: str | None, image_token_env: str | None = IMAGE_TOKEN) -> FastAPI:
    os.environ["AMANE_DATA_DIR"] = str(tmp_path / "data")
    os.environ["AMANE_SAFE_DIRS"] = str(tmp_path / "files")
    os.environ["AMANE_LOG_DIR"] = str(tmp_path / "logs")
    if token_env is None:
        # 空值覆盖 .env.dev 的 off, 走 auto 生成; pop 会让 dotenv 再次生效
        os.environ["AMANE_TOKEN"] = ""
    else:
        os.environ["AMANE_TOKEN"] = token_env
    # 同 token_env: None 走 auto 生成
    os.environ["AMANE_IMAGE_TOKEN"] = "" if image_token_env is None else image_token_env
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


async def _derived_hash(app: FastAPI) -> str:
    """造一个派生资源, 返回 url hash; 图片 token 须能直出它."""

    async def producer(dest: Path) -> bool:
        dest.write_bytes(b"image-bytes")
        return True

    res = await app.state.runtime.resource_store.acquire_derived("https://s/crop.jpg", "crop", "box", producer)
    assert res is not None
    return ResourceStore.url_hash(res.url)


class TestTokenAuth:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_bearer_cookie_and_exemptions(self, token_client):
        client, _ = token_client
        assert (await client.get("libraries")).status_code == 401
        assert (await client.post("webhooks/clouddrive", json={"data": []})).status_code == 401
        assert (await client.get("libraries", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await client.get("system/release")).status_code == 401
        unauthorized = await client.get("libraries")
        assert unauthorized.status_code == 401
        assert "set-cookie" not in unauthorized.headers
        assert (await client.get("health")).status_code == 200
        assert (await client.get("libraries", headers={"Cookie": f"amane_token={TOKEN}"})).status_code == 200
        assert (await client.get("libraries", headers={"Cookie": "amane_token=wrong"})).status_code == 401

        ok = await client.get("libraries", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200
        set_cookie = ok.headers.get("set-cookie", "")
        assert f"amane_token={TOKEN}" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/api" in set_cookie
        # header 认证落下 cookie 后, jar 里的后续裸请求通过
        assert (await client.get("libraries")).status_code == 200

    @pytest.mark.asyncio(loop_scope="function")
    async def test_image_token_scope(self, token_client):
        client, app = token_client
        h = await _derived_hash(app)

        # query 与 Bearer 两种传递方式; 图片 token 不落 cookie
        query_ok = await client.get(f"resources/{h}", params={"token": IMAGE_TOKEN})
        assert query_ok.status_code == 200
        assert "set-cookie" not in query_ok.headers
        bearer_ok = await client.get(f"resources/{h}", headers={"Authorization": f"Bearer {IMAGE_TOKEN}"})
        assert bearer_ok.status_code == 200

        # 只覆盖资源端点
        assert (await client.get("libraries", params={"token": IMAGE_TOKEN})).status_code == 401
        assert (await client.get("libraries", headers={"Authorization": f"Bearer {IMAGE_TOKEN}"})).status_code == 401

        # 错误 / 空 / 非 ASCII 一律 401, 不因比较函数报错变成 500
        assert (await client.get(f"resources/{h}", params={"token": "wrong"})).status_code == 401
        assert (await client.get(f"resources/{h}", params={"token": ""})).status_code == 401
        assert (await client.get(f"resources/{h}", params={"token": "中文"})).status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_image_token_off(self, tmp_path: Path):
        app = make_token_app(tmp_path, TOKEN, image_token_env="off")
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
                assert app.state.runtime.image_token is None
                h = await _derived_hash(app)
                assert (await client.get(f"resources/{h}", params={"token": IMAGE_TOKEN})).status_code == 401
                # 主 token 不受影响
                assert (
                    await client.get(f"resources/{h}", headers={"Authorization": f"Bearer {TOKEN}"})
                ).status_code == 200
        finally:
            await ctx.__aexit__(None, None, None)

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
            # 校验关闭时图片 token 不生成: 资源端点本就免鉴权
            assert app.state.runtime.image_token is None
            async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
                resp = await client.get("libraries")
                assert resp.status_code == 200
        finally:
            await ctx.__aexit__(None, None, None)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_auto_mode_persists_token(self, tmp_path: Path):
        app = make_token_app(tmp_path, None, image_token_env=None)
        token = resolve_api_token(None, tmp_path / "data")
        image_token = resolve_image_token(None, tmp_path / "data")
        assert token is not None
        assert image_token is not None
        assert (tmp_path / "data" / "token").is_file()
        assert (tmp_path / "data" / "image_token").is_file()
        # 再次解析 (模拟重启) 返回同一 token
        assert resolve_api_token(None, tmp_path / "data") == token
        assert resolve_image_token(None, tmp_path / "data") == image_token
        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()
        try:
            assert app.state.runtime.image_token == image_token
            async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
                resp = await client.get("libraries", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 200
                # 模拟新会话 (无 cookie jar): 裸请求仍须 401
                client.cookies.clear()
                resp = await client.get("libraries")
                assert resp.status_code == 401
        finally:
            await ctx.__aexit__(None, None, None)
