"""测试 ResourceStore 的派生资源 (裁剪), 就地超分与获取失败的收尾."""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from amane.media import ResourceStore, derived_locator
from amane.media.resource_store import RESOURCE_URL_PREFIX, _is_placeholder_url, internal_url_hash
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


class TestInternalUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("/api/resources/0123456789abcdef", "0123456789abcdef"),
            ("/api/resources/0123456789abcdef?width=100", "0123456789abcdef"),
            ("/api/resources/0123456789abcdef/extra", "0123456789abcdef"),
            ("/api/resources/", None),
            ("/api/resource/0123456789abcdef", None),
            ("https://site/img.jpg", None),
            ("", None),
        ],
    )
    def test_parse(self, url: str, expected: str | None):
        assert internal_url_hash(url) == expected

    @pytest.mark.asyncio
    async def test_acquire_resolves_without_request(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch
    ):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"derived-bytes")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "box:0,0,10,10", producer)
        assert res is not None
        client, session = _stub_client(monkeypatch, final_url="https://s/t.jpg")
        internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(res.url)}"

        path = await resource_store.acquire(internal, client)

        assert path == resource_store.full_path(res)
        assert session.methods == []  # 内部 URL 不发请求
        assert await resource_store.get_by_url(internal) is None  # 也不写记录

    @pytest.mark.asyncio
    async def test_acquire_internal_missing_returns_none(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch
    ):
        client, session = _stub_client(monkeypatch, final_url="https://s/t.jpg")
        assert await resource_store.acquire(f"{RESOURCE_URL_PREFIX}/deadbeefdeadbeef", client) is None
        assert session.methods == []


class TestResolveSource:
    @pytest.mark.asyncio
    async def test_internal_returns_locator_and_path(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch
    ):
        async def producer(dest: Path) -> bool:
            dest.write_bytes(b"x")
            return True

        res = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
        assert res is not None
        client, session = _stub_client(monkeypatch, final_url="https://s/t.jpg")
        internal = f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(res.url)}"

        src = await resource_store.resolve_source(internal, client)

        assert src is not None
        assert src.locator == res.url  # 底层 locator, 而非内部 URL
        assert src.path == resource_store.full_path(res)
        assert session.methods == []

    @pytest.mark.asyncio
    async def test_external_downloads(self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch):
        client, session = _stub_client(monkeypatch, final_url=_REQUEST, content=b"jpeg-bytes")

        src = await resource_store.resolve_source(_REQUEST, client)

        assert src is not None
        assert src.locator == _REQUEST
        assert src.path.read_bytes() == b"jpeg-bytes"
        assert session.methods[0] == "HEAD"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["data:image/png;base64,AAAA", "ftp://host/img.jpg", "img.jpg", ""])
    async def test_rejects_other_schemes_without_request(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch, url: str
    ):
        client, session = _stub_client(monkeypatch, final_url="https://s/t.jpg")
        assert await resource_store.resolve_source(url, client) is None
        assert session.methods == []


class _StubResponse:
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, *, url: str, content: bytes = b"", status: int = 200) -> None:
        self.url = url
        self.content = content
        self.status_code = status


class _StubSession:
    """替换 ``WebClient._session``: 按方法返回预设应答, 记录出站方法."""

    def __init__(self, *, final_url: str, content: bytes = b"", statuses: dict[str, int] | None = None) -> None:
        self.methods: list[str] = []
        self._final_url = final_url
        self._content = content
        self._statuses = statuses or {}

    async def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.methods.append(method)
        return _StubResponse(url=self._final_url, content=self._content, status=self._statuses.get(method, 200))


def _stub_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    final_url: str,
    content: bytes = b"",
    statuses: dict[str, int] | None = None,
) -> tuple[WebClient, _StubSession]:
    client = WebClient(limiters=RateLimiters(default_rate=100))
    session = _StubSession(final_url=final_url, content=content, statuses=statuses)
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


def _siblings(dest: Path) -> list[Path]:
    """dest 所在目录里除 dest 之外的文件; 用于断言下载的临时文件已清理."""
    if not dest.parent.exists():
        return []
    return sorted(p for p in dest.parent.iterdir() if p != dest)


class TestAcquireFailure:
    """获取失败既可能是站点拦下指纹, 也可能是上游没有该图; 两种失败都不写资源记录, 也不留残缺文件."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [403, 404, 500])
    async def test_failed_download_keeps_no_trace(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch, status: int
    ):
        client, session = _stub_client(monkeypatch, final_url=_REQUEST, statuses={"GET": status})
        dest = resource_store._compute_path(_REQUEST)

        assert await resource_store.acquire(_REQUEST, client) is None

        assert not dest.exists()
        assert _siblings(dest) == []  # 下载用的临时文件也已清理
        assert await resource_store.get_by_url(_REQUEST) is None
        assert session.methods[0] == "HEAD" and "GET" in session.methods

    @pytest.mark.asyncio
    async def test_partial_download_is_removed(self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch):
        """分块下载先按 Content-Length 建文件, 失败会留下只写入一部分的文件: 残片必须落在临时文件上并被清理."""
        client, _ = _stub_client(monkeypatch, final_url=_REQUEST)
        dest = resource_store._compute_path(_REQUEST)
        written: list[Path] = []

        async def partial_download(url: str, target: Path, **kwargs: Any) -> bool:
            written.append(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"half")  # 只写入一部分
            return False

        monkeypatch.setattr(client, "download", partial_download)

        assert await resource_store.acquire(_REQUEST, client) is None

        assert written and written[0] != dest  # 不就地写 dest
        assert written[0].parent == dest.parent  # 同目录, 改名才是原子的
        assert written[0].suffix == dest.suffix  # 保留扩展名: download 的实现可能按扩展名推断格式
        assert not written[0].exists()  # 残片已清理
        assert not dest.exists()
        assert _siblings(dest) == []

    @pytest.mark.asyncio
    async def test_failure_keeps_existing_file(self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch):
        """dest 可能已被同一 URL 的另一个调用写好, 失败方不得删掉它."""
        dest = resource_store._compute_path(_REQUEST)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"complete-bytes")
        client, _ = _stub_client(monkeypatch, final_url=_REQUEST, statuses={"GET": 403})

        assert await resource_store.acquire(_REQUEST, client) is None

        assert dest.read_bytes() == b"complete-bytes"


class TestAcquireConcurrency:
    @pytest.mark.asyncio
    async def test_failed_call_does_not_delete_completed_file(
        self, resource_store: ResourceStore, monkeypatch: pytest.MonkeyPatch
    ):
        """并发获取同一 URL: 失败方要等成功方写完 dest 再失败, 成功方的文件与记录都必须还在."""
        released = asyncio.Event()

        class GatedSession(_StubSession):
            """GET 先等成功方落盘, 保证失败方的收尾发生在 dest 写好之后."""

            async def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
                if method == "GET":
                    await released.wait()
                return await super().request(method, url, **kwargs)

        ok_client, _ = _stub_client(monkeypatch, final_url=_REQUEST, content=b"jpeg-bytes")
        fail_client = WebClient(limiters=RateLimiters(default_rate=100))
        monkeypatch.setattr(fail_client, "_session", GatedSession(final_url=_REQUEST, statuses={"GET": 403}))

        failing = asyncio.create_task(resource_store.acquire(_REQUEST, fail_client))
        try:
            path = await resource_store.acquire(_REQUEST, ok_client)
            assert path is not None and path.read_bytes() == b"jpeg-bytes"
            released.set()

            assert await failing is None

            assert path.read_bytes() == b"jpeg-bytes"
            record = await resource_store.get_by_url(_REQUEST)
            assert record is not None and resource_store.full_path(record) == path
        finally:
            released.set()
            await asyncio.gather(failing, return_exceptions=True)
