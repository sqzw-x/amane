"""测试资源 serve 端点 (哑文件服务 + 外链反代) 与本地上传."""

import io
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from amane.api.routes import resources as resources_route
from amane.media import ResourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx2 import AsyncClient


async def _make_derived(store: ResourceStore) -> tuple[str, str]:
    """造一个裁剪派生资源, 返回 (url_hash, content_hash)."""

    async def producer(dest: Path) -> bool:
        Image.new("RGB", (379, 538), "blue").save(dest)
        return True

    res = await store.acquire_derived("https://s/t.jpg", "crop", "0.7042", producer)
    assert res is not None
    assert res.content_hash is not None
    return ResourceStore.url_hash(res.url), res.content_hash


@pytest.mark.asyncio(loop_scope="function")
async def test_serve_resource_etag(app: FastAPI, client: AsyncClient):
    store = app.state.runtime.resource_store
    h, content_hash = await _make_derived(store)
    etag = f'"{content_hash}"'

    resp = await client.get(f"resources/{h}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["etag"] == etag
    cache_control = resp.headers.get("cache-control", "")
    assert "no-cache" in cache_control
    assert "immutable" not in cache_control
    assert "max-age" not in cache_control
    assert resp.content[:2] == b"\xff\xd8"  # JPEG magic

    cached = await client.get(f"resources/{h}", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.headers["etag"] == etag
    assert "no-cache" in cached.headers.get("cache-control", "")
    assert cached.content == b""

    stale = await client.get(f"resources/{h}", headers={"If-None-Match": '"stale-hash"'})
    assert stale.status_code == 200
    assert len(stale.content) > 0


@pytest.mark.asyncio(loop_scope="function")
async def test_serve_missing_resource_404(client: AsyncClient):
    resp = await client.get("resources/deadbeefdeadbeef")
    assert resp.status_code == 404


class TestProxyImage:
    """GET /resources/proxy - 代理外链图片"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_http_url_rejected(self, client: AsyncClient):
        resp = await client.get("resources/proxy?url=file:///etc/passwd")
        assert resp.status_code == 400
        assert "仅支持 http/https" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upstream_error_and_negative_cache(self, client: AsyncClient, app, monkeypatch):
        """上游失败 → 502 + 负缓存, 二次请求不再打 acquire."""
        mock_store = AsyncMock()
        mock_store.acquire = AsyncMock(return_value=None)
        monkeypatch.setattr(app.state.runtime, "resource_store", mock_store)

        url = "http://example.com/notfound.png"
        first = await client.get(f"resources/proxy?url={url}")
        assert first.status_code == 502
        assert mock_store.acquire.await_count == 1
        assert app.state.runtime.proxy_failure_cache.is_blocked(url)

        second = await client.get(f"resources/proxy?url={url}")
        assert second.status_code == 502
        assert mock_store.acquire.await_count == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_proxy_image_success_and_etag(self, client: AsyncClient, app, monkeypatch, tmp_path):
        """代理成功返回图片 (no-cache + ETag); If-None-Match 命中 → 304."""
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
            b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img_path = tmp_path / "image.png"
        img_path.write_bytes(png_data)

        record = AsyncMock()
        record.content_hash = "abc123hash"

        mock_store = AsyncMock()
        mock_store.acquire = AsyncMock(return_value=img_path)
        mock_store.get_by_url = AsyncMock(return_value=record)
        monkeypatch.setattr(app.state.runtime, "resource_store", mock_store)

        url = "http://example.com/image.png"
        resp = await client.get(f"resources/proxy?url={url}")
        assert resp.status_code == 200
        assert resp.content == png_data
        assert "no-cache" in resp.headers.get("cache-control", "")
        assert resp.headers["etag"] == '"abc123hash"'
        assert not app.state.runtime.proxy_failure_cache.is_blocked(url)

        cached = await client.get(f"resources/proxy?url={url}", headers={"If-None-Match": '"abc123hash"'})
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == '"abc123hash"'
        assert "no-cache" in cached.headers.get("cache-control", "")


class TestUploadResource:
    """POST /resources/upload - 本地上传的图片存成 Resource, 返回可写入图片字段的内部 URL."""

    @staticmethod
    def _png(size: tuple[int, int] = (4, 3)) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", size, "blue").save(buf, format="PNG")
        return buf.getvalue()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upload_then_serve_and_dedupe(self, client: AsyncClient):
        data = self._png()
        resp = await client.post("resources/upload", files={"file": ("poster.png", data, "image/png")})
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert url.startswith("/api/resources/")

        served = await client.get(url.removeprefix("/api/"))
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert served.content == data

        # 同一份字节再传一次: 复用同一条资源.
        again = await client.post("resources/upload", files={"file": ("other.png", data, "image/png")})
        assert again.status_code == 200
        assert again.json()["url"] == url

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disguised_filename_rejected(self, client: AsyncClient):
        """扩展名与 Content-Type 都是 image/png, 内容却不是图片."""
        resp = await client.post(
            "resources/upload", files={"file": ("poster.png", b"definitely-not-an-image", "image/png")}
        )
        assert resp.status_code == 422
        assert "JPEG / PNG / WebP" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unsupported_format_rejected(self, client: AsyncClient):
        buf = io.BytesIO()
        Image.new("RGB", (4, 3), "blue").save(buf, format="GIF")
        resp = await client.post("resources/upload", files={"file": ("poster.gif", buf.getvalue(), "image/gif")})
        assert resp.status_code == 422
        assert "JPEG / PNG / WebP" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_file_rejected(self, client: AsyncClient):
        resp = await client.post("resources/upload", files={"file": ("poster.png", b"", "image/png")})
        assert resp.status_code == 422
        assert "为空" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_oversize_rejected(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        """上限按块截断, 不把整份请求体读进来."""
        monkeypatch.setattr(resources_route, "MAX_IMAGE_UPLOAD_BYTES", 16)
        resp = await client.post("resources/upload", files={"file": ("poster.png", self._png((40, 40)), "image/png")})
        assert resp.status_code == 413
        assert "MB" in resp.json()["detail"]


class TestImageToken:
    """图片 token: 代理只从缓存读取, 未命中不发起请求; 主 token 仍按完整代理流程处理."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_proxy_reads_cache_only(self, client: AsyncClient, app, monkeypatch, tmp_path):
        img_path = tmp_path / "cached.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        record = AsyncMock()
        record.content_hash = "cachedhash"

        store = AsyncMock()
        store.resolve = AsyncMock(return_value=img_path)
        store.get_by_url = AsyncMock(return_value=record)
        store.acquire = AsyncMock()
        monkeypatch.setattr(app.state.runtime, "resource_store", store)
        monkeypatch.setattr(app.state.runtime, "api_token", "main-token")
        monkeypatch.setattr(app.state.runtime, "image_token", "img-token")

        url = "http://example.com/cached.png"
        resp = await client.get("resources/proxy", params={"url": url, "token": "img-token"})
        assert resp.status_code == 200
        assert resp.headers["etag"] == '"cachedhash"'
        assert store.acquire.await_count == 0
        assert not app.state.runtime.proxy_failure_cache.is_blocked(url)

        cached = await client.get(
            "resources/proxy",
            params={"url": url, "token": "img-token"},
            headers={"If-None-Match": '"cachedhash"'},
        )
        assert cached.status_code == 304
        assert cached.content == b""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_proxy_miss_does_not_fetch(self, client: AsyncClient, app, monkeypatch):
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)
        store.acquire = AsyncMock()
        monkeypatch.setattr(app.state.runtime, "resource_store", store)
        monkeypatch.setattr(app.state.runtime, "api_token", "main-token")
        monkeypatch.setattr(app.state.runtime, "image_token", "img-token")

        url = "http://example.com/missing.png"
        resp = await client.get("resources/proxy", params={"url": url, "token": "img-token"})
        assert resp.status_code == 404
        assert "本地缓存" in resp.json()["detail"]
        assert store.acquire.await_count == 0
        assert not app.state.runtime.proxy_failure_cache.is_blocked(url)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_main_token_still_fetches(self, client: AsyncClient, app, monkeypatch, tmp_path):
        img_path = tmp_path / "fetched.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        record = AsyncMock()
        record.content_hash = "fetchedhash"

        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)
        store.get_by_url = AsyncMock(return_value=record)
        store.acquire = AsyncMock(return_value=img_path)
        monkeypatch.setattr(app.state.runtime, "resource_store", store)
        monkeypatch.setattr(app.state.runtime, "api_token", "main-token")
        monkeypatch.setattr(app.state.runtime, "image_token", "img-token")

        url = "http://example.com/fetched.png"
        resp = await client.get(
            "resources/proxy",
            params={"url": url, "token": "img-token"},
            headers={"Authorization": "Bearer main-token"},
        )
        assert resp.status_code == 200
        assert store.acquire.await_count == 1
        assert store.resolve.await_count == 0
