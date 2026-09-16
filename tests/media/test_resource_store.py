"""测试 ResourceStore 的派生资源 (裁剪) 与就地超分能力."""

from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from amane.media import ResourceStore, derived_locator
from amane.media.resource_store import _is_placeholder_url
from amane.net.http import RateLimiters, WebClient

if TYPE_CHECKING:
    from pathlib import Path


def test_derived_locator_deterministic():
    a = derived_locator("https://site/t.jpg", "crop", "0.714")
    b = derived_locator("https://site/t.jpg", "crop", "0.714")
    c = derived_locator("https://site/t.jpg", "crop", "0.666")
    d = derived_locator("https://site/other.jpg", "crop", "0.714")
    assert a == b  # 同输入恒等
    assert a != c  # 参数不同
    assert a != d  # 源不同
    assert a.startswith("derived:")


class TestAcquireDerived:
    @pytest.mark.asyncio
    async def test_generate_then_hit_cache(self, resource_store: ResourceStore):
        calls = {"n": 0}

        async def producer(dest: Path) -> bool:
            calls["n"] += 1
            dest.write_bytes(b"cropped-bytes")
            return True

        r1 = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert r1 is not None
        assert r1.meta == {"op": "crop", "src": "https://s/t.jpg", "args": "0.7"}
        assert r1.size == len(b"cropped-bytes")
        assert calls["n"] == 1

        # 第二次相同参数 → 命中缓存, producer 不再调用
        r2 = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert r2 is not None
        assert r2.url == r1.url
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_producer_failure_returns_none(self, resource_store: ResourceStore):
        async def producer(dest: Path) -> bool:
            return False  # 不生成文件

        r = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert r is None

    @pytest.mark.asyncio
    async def test_different_args_distinct_records(self, resource_store: ResourceStore):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"x")
            return True

        r1 = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        r2 = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.6", producer)
        assert r1 is not None and r2 is not None
        assert r1.url != r2.url


class TestUpscaleInPlace:
    @pytest.mark.asyncio
    async def test_overwrites_and_marks_meta(self, resource_store: ResourceStore):
        # 先造一个派生资源作为超分目标
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"small")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert res is not None
        old_hash = res.content_hash

        async def sr_producer(src: Path, out: Path) -> bool:
            out.write_bytes(b"upscaled-much-larger-bytes")
            return True

        done = await resource_store.upscale_in_place(res, {"tool": "realesrgan", "scale": 4}, sr_producer)
        assert done is True

        updated = await resource_store.get_by_url(res.url)
        assert updated is not None
        assert updated.meta is not None and "sr" in updated.meta
        assert updated.meta["op"] == "crop"  # 保留原派生信息
        assert updated.content_hash != old_hash  # 内容已变
        assert updated.size == len(b"upscaled-much-larger-bytes")

    @pytest.mark.asyncio
    async def test_skips_if_already_sr(self, resource_store: ResourceStore):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"x")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert res is not None

        async def sr_producer(src: Path, out: Path) -> bool:
            out.write_bytes(b"sr1")
            return True

        assert await resource_store.upscale_in_place(res, {"scale": 2}, sr_producer) is True
        updated = await resource_store.get_by_url(res.url)
        assert updated is not None
        # 已有 sr 标记 → 再次调用跳过
        assert await resource_store.upscale_in_place(updated, {"scale": 4}, sr_producer) is False

    @pytest.mark.asyncio
    async def test_producer_failure_keeps_original(self, resource_store: ResourceStore):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"original")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert res is not None

        async def failing_sr(src: Path, out: Path) -> bool:
            return False

        done = await resource_store.upscale_in_place(res, {"scale": 4}, failing_sr)
        assert done is False
        updated = await resource_store.get_by_url(res.url)
        assert updated is not None
        assert updated.meta is not None and "sr" not in updated.meta  # 未标记


class TestGetByUrlHash:
    @pytest.mark.asyncio
    async def test_serve_lookup(self, resource_store: ResourceStore):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"img")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert res is not None

        h = ResourceStore.url_hash(res.url)
        found = await resource_store.get_by_url_hash(h)
        assert found is not None
        record, path = found
        assert record.url == res.url
        assert path.exists()

    @pytest.mark.asyncio
    async def test_missing_hash_returns_none(self, resource_store: ResourceStore):
        assert await resource_store.get_by_url_hash("deadbeefdeadbeef") is None


class _StubResponse:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, *, url: str, content: bytes = b"") -> None:
        self.url = url
        self.content = content


class _StubSession:
    """替换 ``WebClient._session``: 按方法返回预设应答, 记录出站方法."""

    def __init__(self, *, final_url: str, content: bytes = b"") -> None:
        self.methods: list[str] = []
        self._response = _StubResponse(url=final_url, content=content)

    async def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.methods.append(method)
        return self._response


def _stub_client(
    monkeypatch: pytest.MonkeyPatch, *, final_url: str, content: bytes = b""
) -> tuple[WebClient, _StubSession]:
    client = WebClient(limiters=RateLimiters(default_rate=100))
    session = _StubSession(final_url=final_url, content=content)
    monkeypatch.setattr(client, "_session", session)
    return client, session


_REQUEST = "https://pics.dmm.co.jp/digital/video/mide00030/mide00030pl.jpg"
_PLACEHOLDER = "https://pics.dmm.com/mono/movie/n/now_printing/now_printing.jpg"


class TestPlaceholderRedirect:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_PLACEHOLDER, True),
            ("https://imgsrc.dmm.com/pics/mono/movie/n/now_printing/now_printing.jpg?h=800&w=800", True),
            (_REQUEST, False),
            # 只匹配路径, 查询串里的同名字样不算
            ("https://pics.dmm.com/redirect?to=/now_printing/now_printing.jpg", False),
        ],
    )
    def test_detects_placeholder_path(self, url: str, expected: bool):
        assert _is_placeholder_url(url) is expected

    @pytest.mark.asyncio
    async def test_acquire_rejects_placeholder_redirect(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch
    ):
        client, session = _stub_client(monkeypatch, final_url=_PLACEHOLDER, content=b"placeholder-bytes")

        assert await resource_store.acquire(_REQUEST, client) is None
        assert session.methods == ["HEAD"]  # 只探测, 不下载
        assert await resource_store.get_by_url(_REQUEST) is None

    @pytest.mark.asyncio
    async def test_acquire_keeps_plain_redirect(self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch):
        client, session = _stub_client(monkeypatch, final_url=_REQUEST, content=b"jpeg-bytes")

        path = await resource_store.acquire(_REQUEST, client)

        assert path is not None and path.read_bytes() == b"jpeg-bytes"
        assert session.methods[0] == "HEAD" and "GET" in session.methods
        assert await resource_store.get_by_url(_REQUEST) is not None
