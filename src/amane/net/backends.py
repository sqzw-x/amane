"""浏览器后端可用性探测: 分发是否带本地引擎, bsk 是否装好并连上浏览器, solver 是否在应答.

只回答「这个实例能不能用这个后端」, 不代表某个来源可达 —— 那是 ``/network/check`` 的职责.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import httpx2
import structlog

from ..enums import BrowserBackendName
from .bsk import probe_bsk

logger = structlog.get_logger()

# solver 只做协议握手, 不渲染页面: 起浏览器要几十秒, 且真实抓取已有来源连通性检查.
_SOLVER_PROBE_TIMEOUT_S = 1.5
# 本地引擎的模块名; 缺失即当前分发不含该引擎, 与 ``config.manager._require_browser_engine`` 同源.
_MODULE_BACKENDS: dict[BrowserBackendName, str] = {
    BrowserBackendName.PATCHRIGHT: "patchright",
    BrowserBackendName.CAMOUFOX: "camoufox",
}


@dataclass(frozen=True)
class ConnectedBrowser:
    """bsk 已连接的浏览器实例."""

    instance_id: str
    name: str
    extension_version: str | None
    unresponsive: bool


@dataclass(frozen=True)
class BackendProbe:
    """一个后端的可用性; ``detail`` 供界面原样展示."""

    backend: BrowserBackendName
    available: bool
    detail: str | None = None
    version: str | None = None
    browsers: tuple[ConnectedBrowser, ...] = ()


def module_available(module: str) -> bool:
    """只查模块是否可被找到, 不执行导入: 探测端点不应有 import 副作用."""
    try:
        return importlib.util.find_spec(module) is not None
    except ImportError, ValueError:
        return False


def _module_probe(backend: BrowserBackendName, module: str) -> BackendProbe:
    if module_available(module):
        return BackendProbe(backend=backend, available=True, detail=f"已包含 {module}")
    return BackendProbe(backend=backend, available=False, detail=f"当前分发未包含 {module}")


async def probe_solver(
    url: str,
    *,
    timeout_s: float = _SOLVER_PROBE_TIMEOUT_S,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> BackendProbe:
    """对 ``/v1`` 发一次 ``sessions.list``: 确认对端是 FlareSolverr 兼容服务.

    只做端口探测会把「别的服务占了这个端口」误判为可用; 真渲染一次又要起浏览器,
    代价和语义都不适合放在环境自检里. ``transport`` 仅测试注入用.
    """
    endpoint = f"{url.rstrip('/')}/v1"
    try:
        async with httpx2.AsyncClient(timeout=timeout_s, transport=transport, trust_env=False) as client:
            response = await client.post(endpoint, json={"cmd": "sessions.list"})
    except Exception as exc:
        return BackendProbe(
            backend=BrowserBackendName.SOLVER, available=False, detail=f"无法连接 solver: {type(exc).__name__}: {exc}"
        )
    if response.status_code >= 400:
        return BackendProbe(
            backend=BrowserBackendName.SOLVER, available=False, detail=f"solver 返回 HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError:
        return BackendProbe(backend=BrowserBackendName.SOLVER, available=False, detail="solver 应答不是 JSON")
    if not isinstance(payload, dict):
        return BackendProbe(backend=BrowserBackendName.SOLVER, available=False, detail="solver 应答不是对象")
    if payload.get("status") != "ok":
        message = payload.get("message")
        detail = message if isinstance(message, str) and message else "solver 应答不是 ok"
        return BackendProbe(backend=BrowserBackendName.SOLVER, available=False, detail=detail)
    return BackendProbe(backend=BrowserBackendName.SOLVER, available=True, detail=endpoint)


async def probe_backends(*, solver_url: str, bsk_path: str | None = None) -> tuple[BackendProbe, ...]:
    """按固定顺序探测全部后端; ``off`` 不是可缺的后端, 不出现在结果里."""
    probes = [_module_probe(backend, module) for backend, module in _MODULE_BACKENDS.items()]
    status = await probe_bsk(bsk_path)
    probes.append(
        BackendProbe(
            backend=BrowserBackendName.BSK,
            available=status.available,
            detail=status.detail,
            version=status.daemon_version,
            browsers=tuple(
                ConnectedBrowser(
                    instance_id=browser.instance_id,
                    name=browser.name,
                    extension_version=browser.extension_version,
                    unresponsive=browser.unresponsive,
                )
                for browser in status.browsers
            ),
        )
    )
    probes.append(await probe_solver(solver_url))
    return tuple(probes)
