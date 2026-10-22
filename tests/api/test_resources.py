"""测试资源 serve 端点 (哑文件服务 + 外链反代)."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from PIL import Image

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
    async def test_upstream_error_and_negative_cache(self, client: AsyncClient, app, monkeypatch, public_dns: None):
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
    async def test_proxy_image_success_and_etag(
        self, client: AsyncClient, app, monkeypatch, tmp_path, public_dns: None
    ):
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
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert not app.state.runtime.proxy_failure_cache.is_blocked(url)

        cached = await client.get(f"resources/proxy?url={url}", headers={"If-None-Match": '"abc123hash"'})
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == '"abc123hash"'
        assert "no-cache" in cached.headers.get("cache-control", "")

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("url", "detail"),
        [
            ("http://127.0.0.1:8000/api/config", "仅支持公网地址"),
            ("http://169.254.169.254/latest/meta-data/", "仅支持公网地址"),
            ("http://10.0.0.5/a.png", "仅支持公网地址"),
            ("http://[::1]/a.png", "仅支持公网地址"),
            # 数值写法与非法端口不是「地址不公网」, 错误信息要能区分
            ("http://2130706433/a.png", "URL 不受支持"),
            ("http://0177.0.0.1/a.png", "URL 不受支持"),
            ("http://example.com:99999/a.png", "URL 不受支持"),
        ],
    )
    async def test_non_public_target_rejected(
        self, client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch, url: str, detail: str
    ):
        """私网 / 回环 / 元数据地址与不合法目标都在出站前被拒, 不进入下载通道."""
        mock_store = AsyncMock()
        monkeypatch.setattr(app.state.runtime, "resource_store", mock_store)

        resp = await client.get("resources/proxy", params={"url": url})

        assert resp.status_code == 400
        assert resp.json()["detail"] == detail
        assert mock_store.acquire.await_count == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_malformed_url_rejected(self, client: AsyncClient):
        """解析不了的 URL 回 400, 不能落到未捕获异常变成 500."""
        resp = await client.get("resources/proxy", params={"url": "http://[::1/a.png"})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "URL 无法解析"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_resolution_failure_is_upstream_error(
        self, client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
    ):
        """公网域名解析失败属于上游不可达: 回 502 并进入负缓存, 不说成地址不合规."""
        url = "https://pics.example/a.png"

        async def _resolve(host: str, port: int) -> set[str]:
            raise OSError("resolver down")

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        resp = await client.get("resources/proxy", params={"url": url})

        assert resp.status_code == 502
        # 解析失败多为瞬态: 不进 15 分钟负缓存, 网络恢复后同一地址还能重试
        assert not app.state.runtime.proxy_failure_cache.is_blocked(url)


@pytest.mark.asyncio(loop_scope="function")
async def test_resource_text_served_as_attachment(app: FastAPI, client: AsyncClient):
    """资源字节来自第三方: 记录里的 MIME 是 text/html 时也不能按该类型回放."""
    store = app.state.runtime.resource_store
    payload = b"<html><script>alert(1)</script></html>"

    async def producer(dest: Path) -> bool:
        dest.write_bytes(payload)
        return True

    res = await store.acquire_derived("https://evil.example/x.html", "crop", "0", producer, ext=".html")
    assert res is not None

    resp = await client.get(f"resources/{ResourceStore.url_hash(res.url)}")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"] == "attachment"
    assert resp.content == payload
