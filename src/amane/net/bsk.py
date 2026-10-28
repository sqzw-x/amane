"""BrowserSkill (``bsk``) 通道: 经 CLI 驱动用户已连接的浏览器.

同一 session 的命令在 daemon 侧串行, 并发只能靠多 session; 每个 session 对应浏览器里的一个
Agent Window, 因此按并发度而非按来源分配. CLI 不提供 cookie 与请求头注入: cookie 只能先导航到
目标域再用页面上下文写入, 请求头丢弃. 扩展会把自身样式注入页面 DOM, 返回前剥掉.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .errors import FailureKind, FailureReason, RequestFailure, classify_block

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = structlog.get_logger()

BrowserPageResult = tuple[str | None, RequestFailure | None]
"""(html, failure): 成功时 failure 为 None, 失败时 html 为 None."""

# 挑战页轮询间隔、浏览器空闲关闭时限与单次 CLI 调用的固定余量 (秒).
_POLL_INTERVAL_S = 1.0
# Agent Window 对用户可见, 空闲不能像无头引擎那样挂 10 分钟; 会话重启很便宜 (clearance 与 cookie
# 存在 profile 里, 不随窗口消失), 因此留一拍给连续任务收尾即可.
_IDLE_TIMEOUT_S = 30.0
_COMMAND_SLACK_S = 5.0
# get-html 的取回上限; 超限 CLI 会截断并在 stderr 告警, 正文长度按站点规模留足余量.
_MAX_HTML_BYTES = 8 * 1024 * 1024
# 导航刚提交时可能读到中间态 (跳转桩 / 空文档); 低于此长度不当成最终正文.
_MIN_DOCUMENT_BYTES = 4096
# 交互式 Turnstile: 无障碍树里的复选框节点, 角色 + 人机验证字样, 避免误点页面上的其他按钮.
_VERIFY_NODE_RE = re.compile(r"(@e\d+)\s+(?:checkbox|button)\b[^\n]*?(验证|真人|verify|human)", re.IGNORECASE)
# 交互式验证: 两次快照之间至少间隔这么久 (复选框出现得比首屏晚, 探测要持续到截止时间),
# 以及单个页面最多点击几次, 避免把预算耗在同一处反复点.
_CLICK_COOLDOWN_S = 2.0
_MAX_CLICKS = 3
# 扩展注入的样式 (antd cssinjs) 带 data-css-hash / data-rc-order, 页面自身样式不受影响.
_INJECTED_STYLE_RE = re.compile(
    r"<style\b[^>]*\b(?:data-css-hash|data-rc-order)=[\"'][^\"']*[\"'][^>]*>.*?</style>",
    re.IGNORECASE | re.DOTALL,
)


class BskError(Exception):
    """``bsk`` 命令失败; ``code`` 取自 CLI 的 JSON 错误体, 输出不可解析时为 None."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.exit_code = exit_code


@dataclass(frozen=True)
class BskBrowser:
    """已连接的浏览器实例."""

    instance_id: str
    name: str
    extension_version: str | None
    unresponsive: bool


@dataclass(frozen=True)
class BskStatus:
    """连通性探测结果; ``detail`` 供设置页直接展示."""

    available: bool
    detail: str
    daemon_version: str | None = None
    protocol_version: str | None = None
    browsers: tuple[BskBrowser, ...] = ()

    @property
    def connected(self) -> bool:
        return any(not browser.unresponsive for browser in self.browsers)


class _ChallengeUnresolved(Exception):
    """等待期内正文仍被判为拦截页."""


def resolve_bsk_path(explicit: str | None = None) -> str | None:
    """配置里的路径优先, 其次 ``AMANE_BSK_PATH``, 最后 PATH 上的 ``bsk``.

    桌面壳的 PATH 不含登录 shell 的条目, 因此必须允许显式指定; 显式值无效时不回退,
    以免悄悄换成另一个二进制.
    """
    for candidate in (explicit, os.getenv("AMANE_BSK_PATH")):
        if candidate:
            return candidate if Path(candidate).is_file() else None
    return shutil.which("bsk")


def strip_injected_html(html: str) -> str:
    """剥掉扩展注入的样式节点; 纯文本解析不受影响, 但正文体积会减半."""
    cleaned, removed = _INJECTED_STYLE_RE.subn("", html)
    if removed:
        logger.debug("bsk stripped injected styles", nodes=removed, bytes=len(html) - len(cleaned))
    return cleaned


async def probe_bsk(bsk_path: str | None = None, *, timeout_s: float = 10.0) -> BskStatus:
    """探测 CLI、daemon 与浏览器连接; 任何一步失败都只描述原因, 不抛异常."""
    path = bsk_path or resolve_bsk_path()
    if path is None or not Path(path).is_file():
        return BskStatus(available=False, detail="未找到 bsk CLI, 请先安装 BrowserSkill 并连接浏览器扩展")
    try:
        data = await _run(path, ("status",), timeout_s=timeout_s)
    except TimeoutError:
        return BskStatus(available=False, detail="bsk status 超时: daemon 无响应")
    except BskError as exc:
        return BskStatus(available=False, detail=f"bsk 不可用: {exc}")
    browsers = tuple(
        BskBrowser(
            instance_id=_str_field(item, "instance_id") or "",
            name=_str_field(item, "browser_name") or "unknown",
            extension_version=_str_field(item, "extension_version"),
            unresponsive=item.get("unresponsive") is True,
        )
        for item in _dict_list(data.get("browsers"))
    )
    if not any(not browser.unresponsive for browser in browsers):
        return BskStatus(
            available=False, detail="bsk 已安装但浏览器未连接, 请打开浏览器并在扩展中连接", browsers=browsers
        )
    return BskStatus(
        available=True,
        detail="ok",
        daemon_version=_str_field(data, "daemon_version"),
        protocol_version=_str_field(data, "protocol_version"),
        browsers=browsers,
    )


async def _run(path: str, args: Sequence[str], *, timeout_s: float) -> dict[str, object]:
    """执行一条 CLI 命令并解析 JSON 输出; 失败一律抛 ``BskError`` 或 ``TimeoutError``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            *args,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise BskError(f"无法启动 bsk: {exc}", code="spawn_failed") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise BskError(f"bsk {args[0]} 无输出: {detail}", code="empty_output", exit_code=proc.returncode)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BskError(f"bsk {args[0]} 输出不是 JSON: {text[:300]}", code="invalid_output") from exc
    if not isinstance(data, dict):
        raise BskError(f"bsk {args[0]} 输出不是对象: {text[:300]}", code="invalid_output")
    if proc.returncode != 0:
        raise BskError(
            _str_field(data, "message") or f"bsk {args[0]} 失败 (退出码 {proc.returncode})",
            code=_str_field(data, "code"),
            hint=_str_field(data, "hint"),
            exit_code=proc.returncode,
        )
    return {str(key): value for key, value in data.items()}


def _str_field(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _int_field(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _seconds(ms: float) -> str:
    """CLI 的 ``--timeout`` 语法."""
    return f"{max(1, int(ms))}ms"


class _Session:
    """一个 Agent Window 及其标签页与串行锁; ``tabs`` 按来源复用, 保住 clearance."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.session_id: str | None = None
        self.tabs: dict[str, int] = {}
        self.cookies: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        self.active = 0
        self.generation = 0
        self.idle_task: asyncio.Task[None] | None = None

    def reset(self) -> None:
        self.session_id = None
        self.tabs.clear()
        self.cookies.clear()


class BskBackend:
    """``BrowserBackend`` 的 bsk 实现: 多 session 摊并发, 单 session 内串行."""

    def __init__(
        self,
        *,
        bsk_path: str | None = None,
        default_timeout: float,
        max_sessions: int = 2,
        poll_interval: float = _POLL_INTERVAL_S,
        idle_timeout: float = _IDLE_TIMEOUT_S,
        click_cooldown: float = _CLICK_COOLDOWN_S,
    ) -> None:
        self._path = bsk_path
        self._default_timeout = default_timeout
        self._poll_interval = max(0.05, poll_interval)
        self._idle_timeout = idle_timeout
        self._click_cooldown = click_cooldown
        self._sessions = [_Session() for _ in range(max(1, max_sessions))]
        self._cursor = 0
        self._closed = False

    async def get_page(
        self,
        url: str,
        *,
        scope: str,
        cookies: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> BrowserPageResult:
        if self._closed:
            return None, RequestFailure(kind=FailureKind.UNEXPECTED, message="browser backend closed")
        if headers:
            logger.debug("bsk ignores request headers", scope=scope, keys=sorted(headers))
        session = self._next_session(scope)
        session.active += 1
        try:
            async with session.lock:
                return await self._fetch(
                    session,
                    url,
                    scope=scope,
                    cookies={} if cookies is None else dict(cookies),
                    wait_for=wait_for,
                    timeout_ms=timeout if timeout is not None else self._default_timeout,
                )
        finally:
            session.active -= 1
            self._schedule_idle(session)

    def _next_session(self, scope: str) -> _Session:
        """已有该来源标签页的会话优先, 否则取在途最少的会话.

        来源粘住会话才能复用标签页、挑战 clearance 与一次性 cookie 引导; 不同来源分散到
        不同会话才能并发, 因为同一会话的命令在 daemon 侧串行.
        """
        for session in self._sessions:
            if scope in session.tabs:
                return session
        count = len(self._sessions)
        start = self._cursor % count
        self._cursor += 1
        order = [(start + offset) % count for offset in range(count)]
        return self._sessions[min(order, key=lambda index: self._sessions[index].active)]

    async def _fetch(
        self,
        session: _Session,
        url: str,
        *,
        scope: str,
        cookies: dict[str, str],
        wait_for: str | None,
        timeout_ms: float,
    ) -> BrowserPageResult:
        try:
            session_id = await self._ensure_session(session)
            tab_id = await self._ensure_tab(session, scope, session_id)
            await self._apply_cookies(session, session_id, tab_id, scope, url, cookies)
            # 会话与引导导航不计入单次渲染时限, 与本地引擎启动的语义一致.
            deadline = time.monotonic() + timeout_ms / 1000
            await self._navigate(session_id, tab_id, url, deadline)
            html = await self._settle(session_id, tab_id, deadline)
            if wait_for is not None:
                await self._wait_for(session_id, tab_id, wait_for, deadline)
                html = await self._get_html(session_id, tab_id, deadline)
            return strip_injected_html(html), None
        except _ChallengeUnresolved:
            return None, RequestFailure(
                kind=FailureKind.UNEXPECTED,
                message=f"cloudflare challenge not resolved: {url}",
                reason=FailureReason.CLOUDFLARE_CHALLENGE,
            )
        except TimeoutError:
            return None, RequestFailure(kind=FailureKind.TIMEOUT, message=f"browser timeout: {url}")
        except BskError as exc:
            if exc.code == "not_found":
                session.reset()
            return None, self._failure(exc, url)

    def _failure(self, exc: BskError, url: str) -> RequestFailure:
        detail = f"{exc} ({exc.code})" if exc.code else str(exc)
        if exc.code == "spawn_failed":
            return RequestFailure(
                kind=FailureKind.UNEXPECTED,
                message=f"bsk CLI 不可用, 请安装 BrowserSkill 或改用 solver: {detail}",
            )
        if exc.code == "timeout" or (exc.hint is not None and "busy" in exc.hint):
            return RequestFailure(kind=FailureKind.TIMEOUT, message=f"bsk timeout: {url}: {detail}")
        logger.error("bsk page fetch failed", url=url, error=detail)
        return RequestFailure(kind=FailureKind.UNEXPECTED, message=f"bsk error: {detail}")

    async def _ensure_session(self, session: _Session) -> str:
        if session.session_id is not None:
            return session.session_id
        data = await self._call(("session", "start"))
        session_id = _str_field(data, "session_id")
        if not session_id:
            raise BskError("bsk session start 未返回 session_id", code="invalid_output")
        session.session_id = session_id
        logger.debug("bsk session started", session=session_id)
        return session_id

    async def _ensure_tab(self, session: _Session, scope: str, session_id: str) -> int:
        tab_id = session.tabs.get(scope)
        if tab_id is not None:
            return tab_id
        data = await self._call(("tab", "create", "--session", session_id))
        created = _int_field(data, "tab_id")
        if created is None:
            raise BskError("bsk tab create 未返回 tab_id", code="invalid_output")
        session.tabs[scope] = created
        return created

    async def _apply_cookies(
        self,
        session: _Session,
        session_id: str,
        tab_id: int,
        scope: str,
        url: str,
        cookies: dict[str, str],
    ) -> None:
        """先落到目标域再写 cookie: CLI 没有 cookie 接口, 只有页面上下文能写."""
        if not cookies:
            return
        key = (scope, tuple(sorted(cookies.items())))
        if key in session.cookies:
            return
        await self._navigate(session_id, tab_id, url, time.monotonic() + self._default_timeout / 1000)
        script = "; ".join(
            f"document.cookie={json.dumps(f'{name}={value}; path=/')}" for name, value in cookies.items()
        )
        await self._call(("evaluate", script, "--session", session_id, "--tab-id", str(tab_id)))
        session.cookies.add(key)
        logger.debug("bsk cookies applied in page", scope=scope, names=sorted(cookies))

    async def _navigate(self, session_id: str, tab_id: int, url: str, deadline: float) -> None:
        await self._call(
            (
                "navigate",
                url,
                "--session",
                session_id,
                "--tab-id",
                str(tab_id),
                "--wait-until",
                "domcontentloaded",
                "--timeout",
                _seconds(_remaining_ms(deadline)),
            ),
            timeout_s=_remaining(deadline) + _COMMAND_SLACK_S,
        )

    async def _settle(self, session_id: str, tab_id: int, deadline: float) -> str:
        """轮询正文直到挑战消解; 其余拦截原因原样交给 ``HttpClient`` 分类.

        托管挑战通常自行消解, 但循环一段时间后 Cloudflare 会换成要求点击的 Turnstile: 此时按
        ``_CLICK_COOLDOWN_S`` 间隔持续取快照找复选框并原生点击 (复选框常晚于首屏出现, 探测只在
        截止时间停止), 同一个 ref 只点一次. 低于 ``_MIN_DOCUMENT_BYTES`` 的正文视为导航中间态继续等,
        逾期则原样返回, 由上层判定.
        """
        clicked: set[str] = set()
        clicks = 0
        last_probe = 0.0
        while True:
            html = await self._get_html(session_id, tab_id, deadline)
            expired = time.monotonic() >= deadline
            if classify_block(html) is FailureReason.CLOUDFLARE_CHALLENGE:
                if expired:
                    raise _ChallengeUnresolved
                now = time.monotonic()
                if clicks < _MAX_CLICKS and now - last_probe >= self._click_cooldown:
                    last_probe = now
                    if await self._click_verification(session_id, tab_id, deadline, clicked):
                        clicks += 1
            elif len(html) >= _MIN_DOCUMENT_BYTES or expired:
                return html
            await asyncio.sleep(min(self._poll_interval, max(0.0, deadline - time.monotonic())))

    async def _click_verification(self, session_id: str, tab_id: int, deadline: float, clicked: set[str]) -> bool:
        """交互式 Turnstile: 从无障碍树里取复选框 ref 后原生点击.

        复选框在跨域 iframe 里, 选择器够不到, 只有快照 ref 能定位; 点击是输入事件而非 DOM 调用.
        同一个 ref 只点一次 — 页面重绘会换 ref, 没换说明点了也没变化, 再点只是浪费预算.
        """
        try:
            data = await self._call(
                ("snapshot", "--session", session_id, "--tab-id", str(tab_id), "--max-tokens", "4000"),
                timeout_s=_remaining(deadline) + _COMMAND_SLACK_S,
            )
        except BskError as exc:
            logger.debug("bsk snapshot failed (ignored)", error=str(exc))
            return False
        text = _str_field(data, "text") or ""
        match = _VERIFY_NODE_RE.search(text)
        if match is None:
            return False
        ref = match.group(1)
        if ref in clicked:
            return False
        clicked.add(ref)
        try:
            await self._call(("click", ref, "--session", session_id, "--tab-id", str(tab_id)))
        except BskError as exc:
            logger.debug("bsk click failed (ignored)", ref=ref, error=str(exc))
            return False
        logger.info("bsk clicked cloudflare verification", ref=ref)
        return True

    async def _wait_for(self, session_id: str, tab_id: int, selector: str, deadline: float) -> None:
        data = await self._call(
            (
                "wait-for-element",
                "--selector",
                selector,
                "--state",
                "visible",
                "--session",
                session_id,
                "--tab-id",
                str(tab_id),
                "--timeout",
                _seconds(_remaining_ms(deadline)),
            ),
            timeout_s=_remaining(deadline) + _COMMAND_SLACK_S,
        )
        if data.get("satisfied") is False:
            raise TimeoutError(f"bsk wait-for-element 未命中: {selector}")

    async def _get_html(self, session_id: str, tab_id: int, deadline: float) -> str:
        data = await self._call(
            (
                "get-html",
                "--session",
                session_id,
                "--tab-id",
                str(tab_id),
                "--max-bytes",
                str(_MAX_HTML_BYTES),
            ),
            timeout_s=_remaining(deadline) + _COMMAND_SLACK_S,
        )
        html = _str_field(data, "html")
        if html is None:
            raise BskError("bsk get-html 未返回 html", code="invalid_output")
        return html

    async def _call(self, args: Sequence[str], *, timeout_s: float = 30.0) -> dict[str, object]:
        path = self._path or resolve_bsk_path()
        if path is None:
            raise BskError("未找到 bsk CLI", code="spawn_failed")
        return await _run(path, args, timeout_s=timeout_s)

    def _schedule_idle(self, session: _Session) -> None:
        if self._idle_timeout <= 0:
            return
        session.generation += 1
        session.idle_task = asyncio.create_task(self._idle_close(session, session.generation))

    async def _idle_close(self, session: _Session, generation: int) -> None:
        """代际替代取消: 期间又有请求则重新计时, 关闭途中被越过不会中断退出清理."""
        await asyncio.sleep(self._idle_timeout)
        if generation != session.generation:
            return
        if session.active:
            self._schedule_idle(session)
            return
        async with session.lock:
            await self._stop(session)

    async def _stop(self, session: _Session) -> None:
        session_id, session.session_id = session.session_id, None
        session.tabs.clear()
        session.cookies.clear()
        if session_id is None:
            return
        try:
            await self._call(("session", "stop", session_id))
        except (BskError, TimeoutError) as exc:
            logger.debug("bsk session stop failed (ignored)", session=session_id, error=str(exc))

    async def close(self) -> None:
        self._closed = True
        tasks = []
        for session in self._sessions:
            session.generation += 1
            task = session.idle_task
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        await asyncio.gather(*(self._stop_locked(session) for session in self._sessions), return_exceptions=True)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_locked(self, session: _Session) -> None:
        async with session.lock:
            await self._stop(session)


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _remaining_ms(deadline: float) -> float:
    return max(1.0, _remaining(deadline) * 1000)
