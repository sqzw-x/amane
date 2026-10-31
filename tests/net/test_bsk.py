"""bsk 通道: CLI 输出解析, 标签页轮换, cookie 引导, 并发与失败映射."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from amane.net.bsk import (
    BskBackend,
    BskError,
    _parse_protocol,
    _run,
    probe_bsk,
    resolve_bsk_path,
    strip_injected_html,
)
from amane.net.errors import FailureKind, FailureReason

CHALLENGE = "<html><script>window._cf_chl_opt={};</script></html>"
_PAD = "<!--pad-->" * 600
CLEAN = f"<html><body><h1>三上悠亜</h1>{_PAD}</body></html>"
TINY = "<html><body>stub</body></html>"


class _FakeBsk:
    """进程内替代实现: 与真实 CLI 一样用 calls.log 记录参数, 用 scenario.json 编排应答.

    不起子进程, 因此各平台行为一致也快得多; 子进程层由 ``_run`` 自己的用例覆盖.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _config(self) -> dict[str, object]:
        path = self.root / "scenario.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _count(self, command: str) -> int:
        path = self.root / "calls.log"
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line)[0] == command)

    def _mark(self, kind: str, name: str) -> None:
        with (self.root / "events.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{kind} {name} {time.time()}\n")

    def _maybe_fail(self, config: dict[str, object], key: str) -> None:
        payload = config.get(key)
        if not isinstance(payload, dict):
            return
        raise BskError(
            str(payload.get("message", key)),
            code=payload.get("code") if isinstance(payload.get("code"), str) else None,
            hint=payload.get("hint") if isinstance(payload.get("hint"), str) else None,
            exit_code=1,
        )

    async def __call__(self, path: str, args: Sequence[str], timeout_s: float) -> dict[str, object]:
        with (self.root / "calls.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(list(args)) + "\n")
        config = self._config()
        command = args[0]
        if command == "status":
            self._maybe_fail(config, "status_error")
            status = config.get("status")
            if isinstance(status, dict):
                return status
            return {
                "daemon_version": "0.4.0",
                "protocol_version": "1.7",
                "browsers": [
                    {
                        "instance_id": "b1",
                        "browser_name": "edge",
                        "extension_version": "0.4.0",
                        "unresponsive": False,
                    }
                ],
            }
        if command == "session":
            if args[1] == "start":
                self._maybe_fail(config, "session_error")
                session_id = config.get("session_id")
                return {"session_id": session_id if isinstance(session_id, str) else "sess-1"}
            return {"stopped": [args[2]]}
        if command == "tab":
            self._maybe_fail(config, "tab_error")
            return {"tab_id": 100 + self._count("tab")}
        if command == "navigate":
            once = config.pop("navigate_error_once", None)
            if isinstance(once, dict):
                (self.root / "scenario.json").write_text(json.dumps(config), encoding="utf-8")
                raise BskError(
                    str(once.get("message", "navigate failed")),
                    code=once.get("code") if isinstance(once.get("code"), str) else None,
                    hint=once.get("hint") if isinstance(once.get("hint"), str) else None,
                    exit_code=1,
                )
            self._mark("start", command)
            sleep_ms = config.get("sleep_ms")
            await asyncio.sleep(float(sleep_ms) / 1000 if isinstance(sleep_ms, int | float) else 0.0)
            self._mark("end", command)
            self._maybe_fail(config, "navigate_error")
            return {"final_url": args[1], "reached": "domcontentloaded"}
        if command == "evaluate":
            self._maybe_fail(config, "evaluate_error")
            return {"ok": True}
        if command == "snapshot":
            self._maybe_fail(config, "snapshot_error")
            texts = config.get("snapshot_texts")
            if isinstance(texts, list) and texts:
                index = min(self._count("snapshot") - 1, len(texts) - 1)
                return {"text": texts[index], "ref_count": 0}
            return {"text": config.get("snapshot_text", ""), "ref_count": 0}
        if command == "click":
            self._maybe_fail(config, "click_error")
            return {"clicked": args[1]}
        if command == "cooperate":
            self._maybe_fail(config, "cooperate_error")
            context_id = f"ctx-{self._count('cooperate')}"
            tab_id = args[args.index("--tab-id") + 1]
            if config.get("sync_delta"):
                return {"tab_id": int(tab_id), "context_id": context_id, "kind": "delta", "changes": []}
            text = config.get("sync_text", config.get("snapshot_text", ""))
            return {
                "tab_id": int(tab_id),
                "context_id": context_id,
                "kind": "baseline",
                "observation": {"text": text, "ref_count": 0},
            }
        if command == "wait-for-element":
            return {"satisfied": config.get("satisfied", True)}
        if command == "get-html":
            self._maybe_fail(config, "get_html_error")
            pages = config.get("pages")
            page_list = [page for page in pages if isinstance(page, str)] if isinstance(pages, list) else []
            index = min(self._count("get-html") - 1, len(page_list) - 1) if page_list else 0
            return {"html": page_list[index] if page_list else "<html>ok</html>"}
        raise BskError(f"unexpected command: {command}", code="unexpected")


@pytest.fixture
def bsk_env(tmp_path: Path) -> Path:
    """一个目录 + 进程内假 CLI: calls.log 记录调用, scenario.json 编排应答.

    占位文件只为让 ``probe_bsk`` 的"可执行文件存在"检查通过, 真正执行的是注入的 runner.
    """
    (tmp_path / "bsk").write_text("", encoding="utf-8")
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
    click_cooldown: float = 2.0,
    max_tabs: int = 12,
) -> BskBackend:
    return BskBackend(
        bsk_path=str(root / "bsk"),
        runner=_FakeBsk(root),
        default_timeout=timeout,
        max_sessions=max_sessions,
        poll_interval=poll_interval,
        click_cooldown=click_cooldown,
        max_tabs=max_tabs,
    )


@pytest.mark.asyncio
async def test_each_request_navigates_a_fresh_tab(bsk_env: Path) -> None:
    """复用标签页的第二次导航要上下文; 每请求一张空白页才能让导航保持免费."""
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    try:
        assert await backend.get_page("https://a.example/1", scope="a") == (CLEAN, None)
        assert await backend.get_page("https://a.example/2", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert _commands(bsk_env).count("session") == 2
    assert _commands(bsk_env).count("tab") == 2
    navigations = [args for args in _calls(bsk_env) if args[0] == "navigate"]
    tabs = {args[args.index("--tab-id") + 1] for args in navigations}
    assert len(tabs) == 2


@pytest.mark.asyncio
async def test_session_rotates_after_tab_budget(bsk_env: Path) -> None:
    """关标签页也要上下文, 清理靠会话轮换: 标签页到上限时停会话再开新的."""
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env, max_tabs=2)
    try:
        for index in range(3):
            assert await backend.get_page(f"https://a.example/{index}", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    commands = _commands(bsk_env)
    assert commands.count("tab") == 3
    stops = [args for args in _calls(bsk_env) if args[:2] == ["session", "stop"]]
    assert len(stops) == 2  # 轮换一次 + close 收尾一次
    assert commands.count("session") == 4


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
async def test_challenge_tab_is_brought_to_front_once(bsk_env: Path) -> None:
    """窗口里可能堆了多张标签页; 出现挑战时切到那一张, 用户才找得到要验证的页面."""
    _scenario(bsk_env, pages=[CHALLENGE, CHALLENGE, CLEAN])
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    selects = [args for args in _calls(bsk_env) if args[:2] == ["tab", "select"]]
    assert len(selects) == 1
    navigation = next(args for args in _calls(bsk_env) if args[0] == "navigate")
    assert selects[0][2] == navigation[navigation.index("--tab-id") + 1]


@pytest.mark.asyncio
async def test_interactive_challenge_is_clicked_then_clears(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CHALLENGE, CHALLENGE, CLEAN],
        snapshot_text='@e1 checkbox "请验证您是真人"',
        sync_text='@e1 checkbox "请验证您是真人"',
    )
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    clicks = [args for args in _calls(bsk_env) if args[0] == "click"]
    assert len(clicks) == 1
    assert clicks[0][1] == "@e1"
    assert clicks[0][clicks[0].index("--context") + 1].startswith("ctx-")


@pytest.mark.asyncio
async def test_late_checkbox_is_still_clicked(bsk_env: Path) -> None:
    """复选框常晚于首屏出现: 探测要持续到截止时间, 不能只试前几次."""
    _scenario(
        bsk_env,
        pages=[CHALLENGE] * 6 + [CLEAN],
        snapshot_texts=["", "", "", '@e1 checkbox "请验证您是真人"', '@e1 checkbox "请验证您是真人"'],
        sync_text='@e1 checkbox "请验证您是真人"',
    )
    backend = _backend(bsk_env, timeout=3000, poll_interval=0.01, click_cooldown=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    clicks = [args for args in _calls(bsk_env) if args[0] == "click"]
    assert len(clicks) == 1


@pytest.mark.asyncio
async def test_same_ref_is_clicked_once(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CHALLENGE] * 8,
        snapshot_text='@e1 checkbox "请验证您是真人"',
        sync_text='@e1 checkbox "请验证您是真人"',
    )
    backend = _backend(bsk_env, timeout=200, poll_interval=0.01, click_cooldown=0.01)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None and failure is not None and failure.reason is FailureReason.CLOUDFLARE_CHALLENGE
    assert len([args for args in _calls(bsk_env) if args[0] == "click"]) == 1


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
    assert "cooperate" not in _commands(bsk_env)  # 没有复选框就不该付同步的代价


@pytest.mark.asyncio
async def test_click_failure_keeps_polling(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CHALLENGE, CLEAN],
        snapshot_text='@e1 checkbox "请验证您是真人"',
        sync_text='@e1 checkbox "请验证您是真人"',
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
    assert evaluate[0][evaluate[0].index("--context") + 1].startswith("ctx-")


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
    stops = [args for args in _calls(bsk_env) if args[:2] == ["session", "stop"]]
    assert len(stops) == 3  # 两次尝试各收尾一次, 关闭时再停一次
    assert _commands(bsk_env).count("session") == 6


@pytest.mark.asyncio
async def test_control_lost_retries_on_a_fresh_session(bsk_env: Path) -> None:
    """目标页可能把扩展连接拖坏, 同一会话的后续动作全会失败; 丢掉会话重试, 请求仍能成功."""
    _scenario(
        bsk_env,
        pages=[CLEAN],
        navigate_error_once={"code": "permission_denied", "message": "Could not establish connection"},
    )
    backend = _backend(bsk_env)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    stops = [args for args in _calls(bsk_env) if args[:2] == ["session", "stop"]]
    assert len(stops) == 2  # 换会话一次, 关闭时再停一次
    assert _commands(bsk_env).count("session") == 4


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
async def test_backend_reports_unparsable_output(bsk_env: Path) -> None:
    async def broken(path: str, args: Sequence[str], timeout_s: float) -> dict[str, object]:
        raise BskError("bsk session 输出不是 JSON: not-json", code="invalid_output")

    backend = BskBackend(bsk_path=str(bsk_env / "bsk"), runner=broken, default_timeout=500)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None
    assert failure is not None and "not-json" in failure.message


@pytest.mark.asyncio
async def test_probe_reports_connected_browser(bsk_env: Path) -> None:
    status = await probe_bsk(str(bsk_env / "bsk"), runner=_FakeBsk(bsk_env))
    assert status.available and status.connected
    assert status.daemon_version == "0.4.0"
    assert status.browsers[0].name == "edge"


@pytest.mark.asyncio
async def test_probe_without_browser_is_unavailable(bsk_env: Path) -> None:
    _scenario(bsk_env, status={"daemon_version": "0.4.0", "protocol_version": "1.7", "browsers": []})
    status = await probe_bsk(str(bsk_env / "bsk"), runner=_FakeBsk(bsk_env))
    assert not status.available
    assert "浏览器未连接" in status.detail


@pytest.mark.asyncio
async def test_probe_missing_cli_reports_install_hint(tmp_path: Path) -> None:
    status = await probe_bsk(str(tmp_path / "absent"))
    assert not status.available
    assert "未找到 bsk CLI" in status.detail


@pytest.mark.parametrize(("which", "expected"), [("/usr/bin/bsk", "/usr/bin/bsk"), (None, None)])
def test_resolve_bsk_path_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch, which: str | None, expected: str | None
) -> None:
    monkeypatch.setattr("amane.net.bsk.shutil.which", lambda name: which)
    assert resolve_bsk_path() == expected


def test_resolve_bsk_path_prefers_explicit_and_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "bsk"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr("amane.net.bsk.shutil.which", lambda name: "/usr/bin/bsk")

    assert resolve_bsk_path(str(fake)) == str(fake)
    assert resolve_bsk_path(str(tmp_path / "absent")) is None


@pytest.mark.asyncio
async def test_run_rejects_non_object_payload() -> None:
    with pytest.raises(BskError) as excinfo:
        await _run(sys.executable, ("-c", "print('[1,2]')"), timeout_s=10)
    assert excinfo.value.code == "invalid_output"


@pytest.mark.asyncio
async def test_run_rejects_non_json_output() -> None:
    with pytest.raises(BskError) as excinfo:
        await _run(sys.executable, ("-c", "print('not-json')"), timeout_s=10)
    assert excinfo.value.code == "invalid_output"
    assert "not-json" in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_surfaces_cli_error_body_and_exit_code() -> None:
    """真实 CLI 失败时把错误体放在 stdout 并以非 0 退出; 解析后 hint 与 code 都要保留."""
    script = (
        "import json, sys; "
        'print(json.dumps({"code": "not_found", "message": "no session", "hint": "run bsk session list"})); '
        "sys.exit(1)"
    )
    with pytest.raises(BskError) as excinfo:
        await _run(sys.executable, ("-c", script), timeout_s=10)
    assert excinfo.value.code == "not_found"
    assert excinfo.value.hint == "run bsk session list"
    assert excinfo.value.exit_code == 1
    assert "no session" in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_kills_process_after_timeout() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await _run(sys.executable, ("-c", "import time; time.sleep(30)"), timeout_s=0.2)
    assert time.monotonic() - started < 5


def _status(
    browsers: list[dict[str, object]],
    skew: list[dict[str, object]] | None = None,
    *,
    protocol: str = "1.7",
) -> dict[str, object]:
    return {
        "daemon_version": "0.4.0",
        "protocol_version": protocol,
        "browsers": browsers,
        "version_skew_browsers": skew or [],
    }


def _browser(version_skew: bool = False, unresponsive: bool = False) -> dict[str, object]:
    return {
        "instance_id": "b1",
        "browser_name": "edge",
        "extension_version": "0.4.0",
        "unresponsive": unresponsive,
        "version_skew": version_skew,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "available", "fragment"),
    [
        (_status([]), False, "未连接"),
        (_status([_browser(unresponsive=True)]), False, "没有响应"),
        (_status([_browser()]), True, "ok"),
        (_status([_browser(), _browser()]), False, "多个浏览器"),
        (_status([_browser(version_skew=True)]), False, "版本不匹配"),
        (_status([_browser()], [_browser()]), True, "ok"),
        (_status([_browser()], protocol="1.3"), False, "0.4.0"),
    ],
)
async def test_probe_reports_whether_fetching_can_succeed(
    bsk_env: Path, status: dict[str, object], available: bool, fragment: str
) -> None:
    _scenario(bsk_env, status=status)
    result = await probe_bsk(str(bsk_env / "bsk"), runner=_FakeBsk(bsk_env))
    assert result.available is available
    assert fragment in result.detail


@pytest.mark.asyncio
async def test_probe_treats_job_restricted_daemon_as_recoverable(bsk_env: Path) -> None:
    """宿主 Job 不允许 breakaway 时抓取会改起前台 daemon; 探测判死会让用户白折腾."""
    _scenario(
        bsk_env,
        status_error={
            "code": "daemon_start_failed",
            "message": "cannot start an independent Windows daemon; the host may prohibit Job Object breakaway",
        },
    )
    result = await probe_bsk(str(bsk_env / "bsk"), runner=_FakeBsk(bsk_env))
    assert result.available is True
    assert "前台模式" in result.detail


@pytest.mark.asyncio
async def test_small_document_returns_once_it_stops_changing(bsk_env: Path) -> None:
    """小文档 (404 / 空结果) 不该等满整个超时: 连续两次读到相同正文即视为导航完成."""
    _scenario(bsk_env, pages=[TINY, TINY])
    backend = _backend(bsk_env, timeout=5000, poll_interval=0.01)
    started = time.monotonic()
    try:
        assert await backend.get_page("https://a.example/404", scope="a") == (TINY, None)
    finally:
        await backend.close()
    assert time.monotonic() - started < 1.0
    assert _commands(bsk_env).count("get-html") == 2


@pytest.mark.asyncio
async def test_wait_for_keeps_real_budget_after_small_document(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[TINY, TINY, CLEAN])
    backend = _backend(bsk_env, timeout=5000, poll_interval=0.01)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a", wait_for="#row")
    finally:
        await backend.close()
    assert (html, failure) == (CLEAN, None)
    call = next(args for args in _calls(bsk_env) if args[0] == "wait-for-element")
    assert call[call.index("--timeout") + 1] != "1ms"


@pytest.mark.asyncio
async def test_idle_timeout_closes_session_and_next_call_reopens(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN])
    backend = BskBackend(
        bsk_path=str(bsk_env / "bsk"),
        runner=_FakeBsk(bsk_env),
        default_timeout=500,
        idle_timeout=0.05,
    )
    try:
        await backend.get_page("https://a.example/1", scope="a")
        await asyncio.sleep(0.3)
        stops = [args for args in _calls(bsk_env) if args[:2] == ["session", "stop"]]
        assert [args[2] for args in stops] == ["sess-1"]
        await backend.get_page("https://a.example/2", scope="a")
    finally:
        await backend.close()
    assert _commands(bsk_env).count("session") == 4


@pytest.mark.asyncio
async def test_cookie_bootstrap_repeats_per_host(bsk_env: Path) -> None:
    """同一来源跨多个域名时, 每个域名都要真正写入 cookie."""
    _scenario(bsk_env, pages=[CLEAN])
    backend = _backend(bsk_env)
    try:
        await backend.get_page("https://a.example/1", scope="a", cookies={"k": "v"})
        await backend.get_page("https://b.example/1", scope="a", cookies={"k": "v"})
    finally:
        await backend.close()
    assert _commands(bsk_env).count("evaluate") == 2


@pytest.mark.asyncio
async def test_job_restricted_cli_falls_back_to_foreground_daemon(bsk_env: Path) -> None:
    """宿主 Job 不允许脱离时 (Windows 11 的默认会话), CLI 自起 daemon 必然失败, 由后台起前台 daemon."""
    _scenario(
        bsk_env,
        pages=[CLEAN],
        session_error={
            "code": "daemon_start_failed",
            "message": "cannot start an independent Windows daemon; the host may prohibit Job Object breakaway",
        },
    )
    started: list[bool] = []

    async def start_daemon() -> None:
        started.append(True)
        _scenario(bsk_env, pages=[CLEAN])  # 前台 daemon 已就绪, 重试不再撞上启动失败

    backend = BskBackend(
        bsk_path=str(bsk_env / "bsk"),
        runner=_FakeBsk(bsk_env),
        default_timeout=500,
        daemon_starter=start_daemon,
    )
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    assert started == [True]


@pytest.mark.asyncio
async def test_other_failures_do_not_start_daemon(bsk_env: Path) -> None:
    _scenario(bsk_env, pages=[CLEAN], session_error={"code": "not_found", "message": "session not registered"})
    started: list[bool] = []

    async def start_daemon() -> None:
        started.append(True)

    backend = BskBackend(
        bsk_path=str(bsk_env / "bsk"),
        runner=_FakeBsk(bsk_env),
        default_timeout=500,
        daemon_starter=start_daemon,
    )
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None and failure is not None
    assert started == []


@pytest.mark.asyncio
async def test_permission_denied_reports_human_coordination(bsk_env: Path) -> None:
    _scenario(
        bsk_env,
        pages=[CLEAN],
        navigate_error={
            "code": "permission_denied",
            "message": "This action requires the context returned by the latest cooperate sync",
        },
    )
    backend = _backend(bsk_env)
    try:
        html, failure = await backend.get_page("https://a.example/", scope="a")
    finally:
        await backend.close()
    assert html is None and failure is not None
    assert "人工协作" in failure.message


@pytest.mark.asyncio
async def test_cookie_bootstrap_failure_does_not_block_page(bsk_env: Path) -> None:
    """写 cookie 失败只降级: 页面照取, 站点会以被拦的形式暴露真实状态.

    目标页的观察面失效会把整个会话拖坏, 所以要换会话; 同一组合不再重试, 免得每个请求都赔一个会话.
    """
    _scenario(
        bsk_env,
        pages=[CLEAN],
        cooperate_error={"code": "permission_denied", "message": "the user is operating this tab"},
    )
    backend = _backend(bsk_env)
    try:
        assert await backend.get_page("https://a.example/1", scope="a", cookies={"k": "v"}) == (CLEAN, None)
        assert await backend.get_page("https://a.example/2", scope="a", cookies={"k": "v"}) == (CLEAN, None)
    finally:
        await backend.close()
    commands = _commands(bsk_env)
    assert "evaluate" not in commands
    assert commands.count("cooperate") == 1
    assert len([args for args in _calls(bsk_env) if args[:2] == ["session", "stop"]]) == 2


@pytest.mark.asyncio
async def test_click_uses_snapshot_ref_when_sync_is_incremental(bsk_env: Path) -> None:
    """增量同步不带正文; 引用表没变, 沿用快照里的 ref."""
    _scenario(
        bsk_env,
        pages=[CHALLENGE, CLEAN],
        snapshot_text='@e7 checkbox "请验证您是真人"',
        sync_delta=True,
    )
    backend = _backend(bsk_env, poll_interval=0.01)
    try:
        assert await backend.get_page("https://a.example/", scope="a") == (CLEAN, None)
    finally:
        await backend.close()
    clicks = [args for args in _calls(bsk_env) if args[0] == "click"]
    assert len(clicks) == 1 and clicks[0][1] == "@e7"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.7", (1, 7)), ("1.3", (1, 3)), ("2.0.1", (2, 0)), ("1", None), ("1.x", None), ("", None), (None, None)],
)
def test_parse_protocol(raw: str | None, expected: tuple[int, int] | None) -> None:
    assert _parse_protocol(raw) == expected
