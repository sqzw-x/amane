"""浏览器后端: 后端解析, 本地会话复用与挑战等待, solver 错误映射."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AsyncExitStack
from typing import Any

import pytest

from amane.enums import BrowserBackendName
from amane.net.browser import BrowserPageResult, BrowserPool, SolverBackend, _Cookie, _is_timeout, _LocalBackend
from amane.net.errors import FailureKind, FailureReason, RequestError, RequestFailure, classify_request_error


class _Resp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeWeb:
    """minimal WebClient 替身: 记录出站请求, 按编程应答."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        self.calls.append((method, url, kwargs))
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return _Resp(result)


class _GatedWeb(_FakeWeb):
    """在指定 cmd 应答后挂起, 供测试交错 close."""

    def __init__(self, responses: list[object], *, gate_cmd: str = "request.get") -> None:
        super().__init__(responses)
        self.gate_cmd = gate_cmd
        self.get_answered = asyncio.Event()
        self.release = asyncio.Event()

    async def request(self, method: str, url: str, **kwargs: Any) -> _Resp:
        result = await super().request(method, url, **kwargs)
        if kwargs.get("json", {}).get("cmd") == self.gate_cmd:
            self.get_answered.set()
            await self.release.wait()
        return result


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def get_page(self, url: str, **kwargs: Any) -> BrowserPageResult:
        self.calls.append((url, kwargs))
        return "<html>ok</html>", None

    async def close(self) -> None:
        self.closed = True


def _make_pool(backend: BrowserBackendName, *, timeout_ms: float = 1000) -> tuple[BrowserPool, _FakeWeb]:
    web = _FakeWeb([])
    pool = BrowserPool(
        default_backend=backend,
        solver_url="http://solver.test:8191",
        proxy=None,
        web_client=web,  # type: ignore[arg-type]
        timeout_ms=timeout_ms,
    )
    return pool, web


_BACKEND_CASES: list[tuple[BrowserBackendName, BrowserBackendName | None, BrowserBackendName | None]] = [
    # 全局 off: 无覆盖时不可解析, 覆盖可启用.
    (BrowserBackendName.OFF, None, None),
    (BrowserBackendName.OFF, BrowserBackendName.CAMOUFOX, BrowserBackendName.CAMOUFOX),
    # 全局启用: 覆盖优先; 来源显式 off 表示禁用.
    (BrowserBackendName.PATCHRIGHT, None, BrowserBackendName.PATCHRIGHT),
    (BrowserBackendName.PATCHRIGHT, BrowserBackendName.SOLVER, BrowserBackendName.SOLVER),
    (BrowserBackendName.PATCHRIGHT, BrowserBackendName.OFF, None),
]


@pytest.mark.parametrize(("default", "override", "expected"), _BACKEND_CASES)
def test_pool_resolve(
    default: BrowserBackendName, override: BrowserBackendName | None, expected: BrowserBackendName | None
):
    pool, _ = _make_pool(default)

    assert pool.resolve(override) is expected


@pytest.mark.asyncio
async def test_pool_disabled_returns_structured_failure():
    pool, _ = _make_pool(BrowserBackendName.OFF)

    html, failure = await pool.get_page("https://example.com/", scope="site")

    assert html is None
    assert failure is not None
    assert (failure.kind, failure.message) == (FailureKind.UNEXPECTED, "browser backend disabled")


@pytest.mark.asyncio
async def test_pool_closed_returns_structured_failure():
    pool, _ = _make_pool(BrowserBackendName.CAMOUFOX)

    await pool.close()
    html, failure = await pool.get_page("https://example.com/", scope="site")

    assert html is None
    assert failure is not None
    assert failure.message == "browser pool closed"


@pytest.mark.asyncio
async def test_pool_reuses_created_backend_and_applies_timeout(monkeypatch: pytest.MonkeyPatch):
    pool, _ = _make_pool(BrowserBackendName.CAMOUFOX, timeout_ms=4321)
    backend = _RecordingBackend()
    created: list[BrowserBackendName] = []

    def _fake_create(name: BrowserBackendName) -> _RecordingBackend:
        created.append(name)
        return backend

    monkeypatch.setattr(pool, "_create", _fake_create)

    first = await pool.get_page("https://a.example/", scope="site-a")
    second = await pool.get_page("https://b.example/", scope="site-b", timeout=99)

    assert first == ("<html>ok</html>", None)
    assert second == ("<html>ok</html>", None)
    assert created == [BrowserBackendName.CAMOUFOX]
    assert [call[1]["timeout"] for call in backend.calls] == [4321, 99]
    assert [call[1]["scope"] for call in backend.calls] == ["site-a", "site-b"]

    await pool.close()
    assert backend.closed is True


class _FakePage:
    def __init__(self, contents: list[str], *, content_failures: int = 0) -> None:
        self.contents = contents
        self.closed = False
        self.waited: list[str] = []
        self.raise_on_goto: BaseException | None = None
        self.content_error: BaseException | None = None
        self._content_failures = content_failures

    async def goto(self, url: str, *, timeout: float, wait_until: str) -> None:
        if self.raise_on_goto is not None:
            raise self.raise_on_goto

    async def content(self) -> str:
        if self.content_error is not None:
            raise self.content_error
        if self._content_failures > 0:
            self._content_failures -= 1
            raise RuntimeError("execution context destroyed")
        return self.contents[0] if len(self.contents) == 1 else self.contents.pop(0)

    async def wait_for_selector(self, selector: str, *, timeout: float) -> None:
        self.waited.append(selector)

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.cookies: list[_Cookie] = []
        self.headers: dict[str, str] = {}
        self.pages = 0

    async def add_cookies(self, cookies: Sequence[_Cookie]) -> None:
        self.cookies = list(cookies)

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self.headers = headers

    async def new_page(self) -> _FakePage:
        self.pages += 1
        return self.page


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.contexts: list[_FakeContext] = []

    async def new_context(self) -> _FakeContext:
        context = _FakeContext(self.page)
        self.contexts.append(context)
        return context


class _FakeLocalBackend(_LocalBackend):
    """测试替身: 跳过真实浏览器启动, 记录退出栈是否释放."""

    def __init__(self, page: _FakePage, *, idle_timeout: float = 0) -> None:
        super().__init__(proxy=None, default_timeout=1000, idle_timeout=idle_timeout)
        self.browser = _FakeBrowser(page)
        self.closed_browsers = 0
        self.close_started = asyncio.Event()
        self.close_gate = asyncio.Event()
        self.close_gate.set()
        self.browser_closed = asyncio.Event()

    async def _launch(self, stack: AsyncExitStack) -> _FakeBrowser:
        stack.push_async_callback(self._mark_closed)
        return self.browser

    async def _mark_closed(self) -> None:
        self.close_started.set()
        await self.close_gate.wait()
        self.closed_browsers += 1
        self.browser_closed.set()


@pytest.mark.asyncio
async def test_local_backend_reuses_context_per_scope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    page = _FakePage(["<html>ok</html>"])
    backend = _FakeLocalBackend(page)

    await backend.get_page("https://a.example/1", scope="site-a", cookies={"x": "1"}, headers={"R": "1"})
    await backend.get_page("https://a.example/2", scope="site-a")
    await backend.get_page("https://b.example/1", scope="site-b")

    assert len(backend.browser.contexts) == 2
    assert backend.browser.contexts[0].cookies == [{"name": "x", "value": "1", "url": "https://a.example/1"}]
    assert backend.browser.contexts[0].headers == {"R": "1"}
    assert page.closed is True


@pytest.mark.asyncio
async def test_local_backend_waits_for_challenge_to_clear(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    page = _FakePage(["just a moment... cloudflare", "just a moment... cloudflare", "<html>solved</html>"])
    backend = _FakeLocalBackend(page)

    html, failure = await backend.get_page("https://a.example/", scope="site-a")

    assert (html, failure) == ("<html>solved</html>", None)


@pytest.mark.asyncio
async def test_local_backend_retries_transient_content_read_failure(monkeypatch: pytest.MonkeyPatch):
    """导航瞬间读正文失败 (execution context destroyed) 不视为空页."""
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    page = _FakePage(["<html>solved</html>"], content_failures=2)
    backend = _FakeLocalBackend(page)

    html, failure = await backend.get_page("https://a.example/", scope="site-a", timeout=5000)

    assert (html, failure) == ("<html>solved</html>", None)


@pytest.mark.asyncio
async def test_local_backend_reports_unresolved_challenge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    page = _FakePage(["just a moment... cloudflare"])
    backend = _FakeLocalBackend(page)

    html, failure = await backend.get_page("https://a.example/", scope="site-a", timeout=50)

    assert html is None
    assert failure is not None
    assert (failure.kind, failure.reason) == (FailureKind.UNEXPECTED, FailureReason.CLOUDFLARE_CHALLENGE)


@pytest.mark.asyncio
async def test_local_backend_maps_timeout():
    page = _FakePage(["<html>ok</html>"])
    page.raise_on_goto = TimeoutError()
    backend = _FakeLocalBackend(page)

    html, failure = await backend.get_page("https://a.example/", scope="site-a")

    assert html is None
    assert failure is not None
    assert failure.kind == FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_local_backend_close_releases_browser():
    backend = _FakeLocalBackend(_FakePage(["<html>ok</html>"]))
    await backend.get_page("https://a.example/", scope="site-a")

    await backend.close()

    assert backend.closed_browsers == 1


@pytest.mark.asyncio
async def test_local_backend_reports_unreadable_page(monkeypatch: pytest.MonkeyPatch):
    """整个等待期读不到正文: 报页面不可读, 不冒充 cloudflare_challenge."""
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    backend = _FakeLocalBackend(_FakePage(["<html>never</html>"], content_failures=10**9))

    html, failure = await backend.get_page("https://a.example/", scope="site-a", timeout=50)

    assert html is None
    assert failure is not None
    assert failure.kind == FailureKind.UNEXPECTED
    assert failure.reason != FailureReason.CLOUDFLARE_CHALLENGE
    assert "browser page unreadable" in failure.message


@pytest.mark.asyncio
async def test_local_backend_unreadable_timeout_maps_to_timeout(monkeypatch: pytest.MonkeyPatch):
    """页面持续不可读且底层是超时类错误: 归为 TIMEOUT."""
    monkeypatch.setattr("amane.net.browser._CHALLENGE_POLL_S", 0.0)
    page = _FakePage(["<html>never</html>"])
    page.content_error = type("TimeoutError", (Exception,), {})("engine timeout")
    backend = _FakeLocalBackend(page)

    html, failure = await backend.get_page("https://a.example/", scope="site-a", timeout=50)

    assert html is None
    assert failure is not None
    assert failure.kind == FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_local_backend_idle_closes_browser():
    backend = _FakeLocalBackend(_FakePage(["<html>ok</html>"]), idle_timeout=0.01)

    await backend.get_page("https://a.example/", scope="site-a")

    await asyncio.wait_for(backend.browser_closed.wait(), timeout=2)
    assert backend.closed_browsers == 1


@pytest.mark.asyncio
async def test_local_backend_idle_close_not_interrupted_by_new_request():
    """关闭途中到来的新请求不得中断退出清理, 旧栈须完整关闭一次."""
    backend = _FakeLocalBackend(_FakePage(["<html>ok</html>"]), idle_timeout=0.01)
    backend.close_gate.clear()

    await backend.get_page("https://a.example/", scope="site-a")
    await asyncio.wait_for(backend.close_started.wait(), timeout=2)

    second = asyncio.create_task(backend.get_page("https://b.example/", scope="site-b"))
    await asyncio.sleep(0)
    backend.close_gate.set()

    assert await second == ("<html>ok</html>", None)
    assert backend.closed_browsers == 1


@pytest.mark.asyncio
async def test_local_backend_closed_rejects_restart():
    backend = _FakeLocalBackend(_FakePage(["<html>ok</html>"]))
    await backend.get_page("https://a.example/", scope="site-a")

    await backend.close()
    html, failure = await backend.get_page("https://b.example/", scope="site-b")

    assert html is None
    assert failure is not None
    assert failure.message == "browser backend closed"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), True),
        (type("TimeoutError", (Exception,), {})("engine"), True),
        (RuntimeError("boom"), False),
    ],
)
def test_is_timeout(error: BaseException, expected: bool):
    """playwright 的 TimeoutError 不继承内建异常, 按类名识别."""
    assert _is_timeout(error) is expected


# solver: cmd 序列, 会话复用, 缺失会话重建, 错误映射.

_SOLVER_OK = {"status": "ok", "solution": {"response": "<html>ok</html>", "status": 200}}
_SOLVER_BAD = {"status": "error", "message": "Challenge not solved"}


def _make_solver(responses: list[object]) -> tuple[SolverBackend, _FakeWeb]:
    web = _FakeWeb(responses)
    return SolverBackend(web_provider=lambda: web, url="http://solver.test:8191/", default_timeout=1000), web  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_solver_creates_session_and_reuses_it():
    solver, web = _make_solver([{"status": "ok"}, _SOLVER_OK, _SOLVER_OK])

    first = await solver.get_page("https://a.example/1", scope="site-a")
    second = await solver.get_page("https://a.example/2", scope="site-a")

    assert first == ("<html>ok</html>", None)
    assert second == ("<html>ok</html>", None)
    assert [(call[0], call[2]["json"]["cmd"]) for call in web.calls] == [
        ("POST", "sessions.create"),
        ("POST", "request.get"),
        ("POST", "request.get"),
    ]
    assert [call[2]["json"].get("session") for call in web.calls] == ["site-a", "site-a", "site-a"]
    assert all(call[1] == "http://solver.test:8191/v1" for call in web.calls)
    assert all(call[2]["use_proxy"] is False for call in web.calls)
    assert all(call[2]["max_attempts"] == 1 for call in web.calls)


@pytest.mark.asyncio
async def test_solver_tolerates_existing_session():
    solver, web = _make_solver([{"status": "error", "message": "Session already exists"}, _SOLVER_OK])

    html, failure = await solver.get_page("https://a.example/", scope="site-a")

    assert (html, failure) == ("<html>ok</html>", None)
    assert [call[2]["json"]["cmd"] for call in web.calls] == ["sessions.create", "request.get"]


@pytest.mark.asyncio
async def test_solver_recreates_missing_session_once():
    solver, web = _make_solver(
        [{"status": "ok"}, {"status": "error", "message": "Session not found"}, {"status": "ok"}, _SOLVER_OK]
    )

    html, failure = await solver.get_page("https://a.example/", scope="site-a")

    assert (html, failure) == ("<html>ok</html>", None)
    assert [call[2]["json"]["cmd"] for call in web.calls] == [
        "sessions.create",
        "request.get",
        "sessions.create",
        "request.get",
    ]


_SOLVER_FAILURES: list[tuple[object, FailureKind, FailureReason]] = [
    (
        {"status": "error", "message": "Challenge not solved"},
        FailureKind.UNEXPECTED,
        FailureReason.CLOUDFLARE_CHALLENGE,
    ),
    ({"status": "error", "message": "Timeout after 60000 ms"}, FailureKind.TIMEOUT, FailureReason.TIMEOUT),
    ({"status": "error", "message": "unexpected"}, FailureKind.UNEXPECTED, FailureReason.UNEXPECTED),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "kind", "reason"), _SOLVER_FAILURES)
async def test_solver_maps_errors(payload: object, kind: FailureKind, reason: FailureReason):
    solver, _ = _make_solver([{"status": "ok"}, payload])

    html, failure = await solver.get_page("https://a.example/", scope="site-a")

    assert html is None
    assert failure is not None
    assert failure.kind == kind
    assert classify_request_error(failure) is reason


@pytest.mark.asyncio
async def test_solver_forwards_http_failure():
    failure = RequestFailure(kind=FailureKind.CURL, message="connection refused")
    solver, _ = _make_solver([RequestError("http://solver.test:8191/v1", failure)])

    html, returned = await solver.get_page("https://a.example/", scope="site-a")

    assert html is None
    assert returned is failure


_SOLVER_MALFORMED: list[tuple[object, str]] = [
    ({"status": "ok"}, "solver response has no solution"),
    ({"status": "ok", "solution": {"response": 123}}, "solver solution has no response"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "message"), _SOLVER_MALFORMED)
async def test_solver_rejects_malformed_success(payload: object, message: str):
    solver, _ = _make_solver([{"status": "ok"}, payload])

    html, failure = await solver.get_page("https://a.example/", scope="site-a")

    assert html is None
    assert failure is not None
    assert (failure.kind, failure.message) == (FailureKind.UNEXPECTED, message)


@pytest.mark.asyncio
async def test_solver_closed_rejects_request():
    solver, _ = _make_solver([])

    await solver.close()
    html, failure = await solver.get_page("https://a.example/", scope="site-a")

    assert html is None
    assert failure is not None
    assert failure.message == "solver backend closed"


@pytest.mark.asyncio
async def test_solver_close_during_request_prevents_session_recreate():
    """close 发生在缺会话回退之前: 不重建会话, 也不写回 _sessions."""
    web = _GatedWeb([{"status": "ok"}, {"status": "error", "message": "Session not found"}, {"status": "ok"}])
    solver = SolverBackend(
        web_provider=lambda: web,  # type: ignore[arg-type]
        url="http://solver.test:8191",
        default_timeout=1000,
    )
    task = asyncio.create_task(solver.get_page("https://a.example/", scope="site-a"))

    await web.get_answered.wait()
    await solver.close()
    web.release.set()

    html, failure = await task

    assert html is None
    assert failure is not None
    assert failure.message == "solver backend closed"
    assert solver._sessions == set()
    assert [call[2]["json"]["cmd"] for call in web.calls] == [
        "sessions.create",
        "request.get",
        "sessions.destroy",
    ]


@pytest.mark.asyncio
async def test_solver_close_after_session_create_recycles_it():
    """create 返回后才 close: 立即销毁该会话, 不写回 _sessions."""
    web = _GatedWeb([{"status": "ok"}, {"status": "ok"}], gate_cmd="sessions.create")
    solver = SolverBackend(
        web_provider=lambda: web,  # type: ignore[arg-type]
        url="http://solver.test:8191",
        default_timeout=1000,
    )
    task = asyncio.create_task(solver.get_page("https://a.example/", scope="site-a"))

    await web.get_answered.wait()
    await solver.close()
    web.release.set()

    html, failure = await task

    assert html is None
    assert failure is not None
    assert failure.message == "solver backend closed"
    assert solver._sessions == set()
    assert [call[2]["json"]["cmd"] for call in web.calls] == ["sessions.create", "sessions.destroy"]


@pytest.mark.asyncio
async def test_solver_close_destroys_all_sessions():
    solver, web = _make_solver(
        [
            {"status": "ok"},
            _SOLVER_OK,
            {"status": "ok"},
            _SOLVER_OK,
            {"status": "ok"},
            {"status": "ok"},
        ]
    )

    await solver.get_page("https://a.example/", scope="site-a")
    await solver.get_page("https://b.example/", scope="site-b")
    await solver.close()

    destroyed = {call[2]["json"]["session"] for call in web.calls if call[2]["json"]["cmd"] == "sessions.destroy"}
    assert destroyed == {"site-a", "site-b"}
