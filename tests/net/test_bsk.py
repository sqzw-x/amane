"""bsk 通道: CLI 输出解析, 会话复用, cookie 引导, 并发与失败映射."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from amane.net.bsk import BskBackend, BskError, _run, probe_bsk, resolve_bsk_path, strip_injected_html
from amane.net.errors import FailureKind, FailureReason

CHALLENGE = "<html><script>window._cf_chl_opt={};</script></html>"
_PAD = "<!--pad-->" * 600
CLEAN = f"<html><body><h1>三上悠亜</h1>{_PAD}</body></html>"
TINY = "<html><body>stub</body></html>"

_FAKE_CLI = """#!{python}
import json, os, sys, time
from pathlib import Path

root = Path(os.environ["FAKE_BSK_DIR"])
args = sys.argv[1:]
with (root / "calls.log").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
cfg = json.loads((root / "scenario.json").read_text(encoding="utf-8")) if (root / "scenario.json").exists() else {}

def count(cmd):
    n = 0
    for line in (root / "calls.log").read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line)[0] == cmd:
                n += 1
        except Exception:
            pass
    return n

def mark(kind, name):
    with (root / "events.log").open("a", encoding="utf-8") as fh:
        fh.write("%s %s %f\\n" % (kind, name, time.time()))

def emit(payload, code=0):
    print(json.dumps(payload))
    sys.exit(code)

def error(key):
    if key in cfg:
        emit(cfg[key], cfg[key].get("exit_code", 1))

cmd = args[0]
if cmd == "status":
    error("status_error")
    emit(cfg.get("status", {"daemon_version": "0.3.3", "protocol_version": "1.3", "browsers": [
        {"instance_id": "b1", "browser_name": "edge", "extension_version": "0.3.3", "unresponsive": False}]}))
if cmd == "session":
    if args[1] == "start":
        error("session_error")
        emit({"session_id": cfg.get("session_id", "sess-1")})
    emit({"stopped": [args[2]]})
if cmd == "tab":
    error("tab_error")
    emit({"tab_id": 100 + count("tab")})
if cmd == "navigate":
    mark("start", cmd)
    time.sleep(cfg.get("sleep_ms", 0) / 1000)
    mark("end", cmd)
    error("navigate_error")
    emit({"final_url": args[1], "reached": "domcontentloaded"})
if cmd == "evaluate":
    error("evaluate_error")
    emit({"ok": True})
if cmd == "snapshot":
    error("snapshot_error")
    emit({"text": cfg.get("snapshot_text", ""), "ref_count": 0})
if cmd == "click":
    error("click_error")
    emit({"clicked": args[1]})
if cmd == "wait-for-element":
    emit({"satisfied": cfg.get("satisfied", True)})
if cmd == "get-html":
    error("get_html_error")
    pages = cfg.get("pages", ["<html>ok</html>"])
    emit({"html": pages[min(count("get-html") - 1, len(pages) - 1)]})
emit({"unexpected": cmd}, 2)
"""


@pytest.fixture
def bsk_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """装一个假 CLI: calls.log 记录调用, scenario.json 编排应答."""
    script = tmp_path / "bsk"
    script.write_text(_FAKE_CLI.replace("{python}", sys.executable), encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_BSK_DIR", str(tmp_path))
    return tmp_path


def _scenario(root: Path, **kwargs: object) -> None:
    (root / "scenario.json").write_text(json.dumps(kwargs), encoding="utf-8")


def _calls(root: Path) -> list[list[str]]:
    path = root / "calls.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _commands(root: Path) -> list[str]:
    return [args[0] for args in _calls(root)]


def _navigate_spans(root: Path) -> list[tuple[float, float]]:
    """假 CLI 记下的 navigate 起止时刻, 用来判断两次导航是否真的交错."""
    starts: list[float] = []
    ends: list[float] = []
    for line in (root / "events.log").read_text(encoding="utf-8").splitlines():
        kind, _, stamp = line.split()
        (starts if kind == "start" else ends).append(float(stamp))
    assert len(starts) == len(ends) == 2
    return list(zip(sorted(starts), sorted(ends), strict=True))


def _backend(
    root: Path,
    *,
    timeout: float = 500,
    max_sessions: int = 2,
    poll_interval: float = 1.0,
) -> BskBackend:
    return BskBackend(
        bsk_path=str(root / "bsk"),
        default_timeout=timeout,
        max_sessions=max_sessions,
        poll_interval=poll_interval,
    )


@pytest.mark.asyncio
async def test_get_page_returns_html_and_reuses_session_and_tab(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    try:
        assert await backend.get_page("https://a.example/1", scope="a") == (CLEAN, None)
        assert await backend.get_page("https://a.example/2", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert _commands(bsk_env).count("session") == 2
    assert _commands(bsk_env).count("tab") == 1


@pytest.mark.asyncio
async def test_separate_scope_gets_its_own_tab(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    try:
        await backend.get_page("https://a.example/1", scope="a")
        await backend.get_page("https://b.example/1", scope="b")
    finally:
        await backend.close()
    assert _commands(bsk_env).count("tab") == 2


@pytest.mark.asyncio
async def test_challenge_polled_until_cleared(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CHALLENGE, CHALLENGE, CLEAN])
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert _commands(bsk_env).count("get-html") == 3


@pytest.mark.asyncio
async def test_interactive_challenge_is_clicked_then_clears(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CHALLENGE, CHALLENGE, CLEAN],
        snapshot_text='@e1 checkbox "请验证您是真人"',
    )
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    clicks = [args for args in _calls(bsk_env) if args[0] == "click"]
    assert len(clicks) == 1
    assert clicks[0][1] == "@e1"


@pytest.mark.asyncio
async def test_challenge_without_verification_node_never_clicks(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CHALLENGE], snapshot_text='@e1 button "setting"')
    backend = _backend(bsk_env, timeout=150, poll_interval=0.01)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and failure.reason is FailureReason.CLOUDFLARE_CHALLENGE
    assert not [args for args in _calls(bsk_env) if args[0] == "click"]


@pytest.mark.asyncio
async def test_click_failure_keeps_polling(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CHALLENGE, CLEAN],
        snapshot_text='@e1 checkbox "请验证您是真人"',
        click_error={"code": "not_found", "message": "ref is stale", "exit_code": 1},
    )
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unresolved_challenge_reports_reason(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CHALLENGE])
    backend = _backend(bsk_env, timeout=120, poll_interval=0.01)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and failure.reason is FailureReason.CLOUDFLARE_CHALLENGE


@pytest.mark.asyncio
async def test_cookies_applied_once_per_scope(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    cookies = {"age_verified": "1"}
    try:
        await backend.get_page("https://a.example/1", scope="a", cookies=cookies)
        await backend.get_page("https://a.example/2", scope="a", cookies=cookies)
    finally:
        await backend.close()
    evaluate = [args for args in _calls(bsk_env) if args[0] == "evaluate"]
    assert len(evaluate) == 1
    assert "age_verified=1; path=/" in evaluate[0][1]


@pytest.mark.asyncio
async def test_wait_for_selector_requests_element_then_refetches(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    try:
        assert await backend.get_page("https://a.example/", scope="a", wait_for="div.act-profile") == (CLEAN, None)
    finally:
        await backend.close()
    wait = [args for args in _calls(bsk_env) if args[0] == "wait-for-element"]
    assert wait and wait[0][wait[0].index("--selector") + 1] == "div.act-profile"
    assert _commands(bsk_env).count("get-html") == 2


@pytest.mark.asyncio
async def test_wait_for_selector_unsatisfied_maps_to_timeout(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN], satisfied=False)
    backend = _backend(bsk_env)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a", wait_for="#missing")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and failure.kind is FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_transient_short_document_is_retried(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[TINY, CLEAN])
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert _commands(bsk_env).count("get-html") == 2


@pytest.mark.asyncio
async def test_short_document_returned_when_budget_runs_out(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[TINY])
    backend = _backend(bsk_env, timeout=100, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (TINY, None)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_injected_styles_stripped(bsk_env: Path) -> None:
    page = f'<html><head><style data-css-hash="x">.a{{}}</style></head><body>ok{_PAD}</body></html>'
    _scenario(bsk_env, pages=[page])
    backend = _backend(bsk_env)
    try:
        html, _ = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is not None
    assert "data-css-hash" not in html
    assert "<body>ok" in html


def test_strip_injected_html_keeps_page_styles() -> None:
    page = '<html><head><style>.page{}</style><style data-rc-order="prepend">.x{}</style></head></html>'
    assert strip_injected_html(page) == "<html><head><style>.page{}</style></head></html>"


@pytest.mark.asyncio
async def test_missing_cli_reports_actionable_failure(tmp_path: Path) -> None:
    backend = BskBackend(bsk_path=str(tmp_path / "absent"), default_timeout=500)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and failure.kind is FailureKind.UNEXPECTED
    assert "bsk CLI 不可用" in failure.message


@pytest.mark.asyncio
async def test_session_busy_maps_to_timeout(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CLEAN],
        navigate_error={
            "code": "timeout",
            "message": "session already has an unfinished command",
            "hint": "wait for the current command to finish",
            "exit_code": 4,
        },
    )
    backend = _backend(bsk_env)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and failure.kind is FailureKind.TIMEOUT


@pytest.mark.asyncio
async def test_lost_session_restarts_on_next_call(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CLEAN],
        get_html_error={"code": "not_found", "message": "session not registered", "exit_code": 1},
    )
    backend = _backend(bsk_env)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
        assert html is None and failure is not None and "not_found" in failure.message
        _scenario(bsk_env, pages=[CLEAN])
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert _commands(bsk_env).count("session") == 3  # 两次 start + 一次 stop


@pytest.mark.asyncio
async def test_single_session_serializes_concurrent_pages(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN], sleep_ms=80)
    backend = _backend(bsk_env, max_sessions=1)
    try:
        results = await asyncio.gather(
            backend.get_page("https://a.example/1", scope="a"),
            backend.get_page("https://a.example/2", scope="b"),
        )
    finally:
        await backend.close()
    assert all(result == (CLEAN, None) for result in results)
    first, second = _navigate_spans(bsk_env)
    assert second[0] >= first[1]  # 同一会话排队: 第二次导航在第一次结束后才开始


@pytest.mark.asyncio
async def test_two_sessions_run_concurrently(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN], sleep_ms=200)
    backend = _backend(bsk_env, max_sessions=2)
    try:
        await asyncio.gather(
            backend.get_page("https://a.example/1", scope="a"),
            backend.get_page("https://a.example/2", scope="b"),
        )
    finally:
        await backend.close()
    first, second = _navigate_spans(bsk_env)
    assert second[0] < first[1]  # 两个会话各自导航, 区间必须重叠


@pytest.mark.asyncio
async def test_close_stops_started_sessions(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    await backend.get_page("https://a.example/", scope="a")
    await backend.close()
    assert _commands(bsk_env).count("session") == 2  # start + stop
    html, failure = await backend.get_page("https://a.example/", scope="a")
    assert html is None and failure is not None and "closed" in failure.message


@pytest.mark.asyncio
async def test_invalid_cli_output_is_a_failure(bsk_env: Path) -> None:
    script = bsk_env / "broken"
    script.write_text("#!/bin/sh\necho not-json\n", encoding="utf-8")
    script.chmod(0o755)
    backend = BskBackend(bsk_path=str(script), default_timeout=500)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and "not-json" in failure.message


@pytest.mark.asyncio
async def test_probe_reports_connected_browser(bsk_env: Path) -> None:
    status = await probe_bsk(str(bsk_env / "bsk"))
    assert status.available and status.connected
    assert status.daemon_version == "0.3.3"
    assert status.browsers[0].name == "edge"


@pytest.mark.asyncio
async def test_probe_without_browser_is_unavailable(bsk_env: Path) -> None:
    _scenario(bsk_env, status={"daemon_version": "0.3.3", "protocol_version": "1.3", "browsers": []})
    status = await probe_bsk(str(bsk_env / "bsk"))
    assert not status.available
    assert "浏览器未连接" in status.detail


@pytest.mark.asyncio
async def test_probe_missing_cli_reports_install_hint(tmp_path: Path) -> None:
    status = await probe_bsk(str(tmp_path / "absent"))
    assert not status.available
    assert "未找到 bsk CLI" in status.detail


def test_resolve_bsk_path_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "bsk"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("AMANE_BSK_PATH", str(fake))
    assert resolve_bsk_path() == str(fake)
    monkeypatch.setenv("AMANE_BSK_PATH", str(tmp_path / "missing"))
    assert resolve_bsk_path() is None


@pytest.mark.asyncio
async def test_run_rejects_non_object_payload(bsk_env: Path) -> None:
    script = bsk_env / "listy"
    script.write_text("#!/bin/sh\necho '[1,2]'\n", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(BskError) as excinfo:
        await _run(str(script), ("status",), timeout_s=5)
    assert excinfo.value.code == "invalid_output"
