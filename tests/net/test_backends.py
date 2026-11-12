"""后端可用性探测: 模块检查, solver 协议握手与结果编排."""

from __future__ import annotations

import httpx2
import pytest

from amane.enums import BrowserBackendName
from amane.net import backends
from amane.net.backends import probe_backends, probe_solver
from amane.net.bsk import BskBrowser, BskStatus


def _transport(handler: object) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_solver_probe_accepts_ok_handshake() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "http://127.0.0.1:8191/v1"
        return httpx2.Response(200, json={"status": "ok", "sessions": []})

    probe = await probe_solver("http://127.0.0.1:8191/", transport=_transport(handler))

    assert probe.available
    assert probe.detail == "http://127.0.0.1:8191/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx2.Response(500, text="boom"), "HTTP 500"),
        (httpx2.Response(200, text="<html>not json</html>"), "不是 JSON"),
        (httpx2.Response(200, json=["x"]), "不是对象"),
        (httpx2.Response(200, json={"status": "error", "message": "Invalid command"}), "Invalid command"),
    ],
)
async def test_solver_probe_reports_protocol_errors(response: httpx2.Response, expected: str) -> None:
    probe = await probe_solver("http://127.0.0.1:8191", transport=_transport(lambda request: response))

    assert not probe.available
    assert expected in (probe.detail or "")


@pytest.mark.asyncio
async def test_solver_probe_reports_unreachable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    probe = await probe_solver("http://127.0.0.1:8191", transport=_transport(handler))

    assert not probe.available
    assert "无法连接 solver" in (probe.detail or "")


class _OkClient:
    """把 probe_solver 的出站换成固定应答, 用来在 probe_backends 里走完整链路."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _OkClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: object = None) -> httpx2.Response:
        return httpx2.Response(200, json={"status": "ok"})


@pytest.mark.asyncio
async def test_probe_backends_covers_every_backend_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "module_available", lambda module: module == "patchright")
    monkeypatch.setattr(backends.httpx2, "AsyncClient", _OkClient)

    async def fake_probe(path: str | None) -> BskStatus:
        assert path == "/opt/bsk"
        return BskStatus(
            available=True,
            detail="ok",
            daemon_version="0.3.3",
            protocol_version="1.3",
            browsers=(BskBrowser(instance_id="b1", name="edge", extension_version="0.3.3", unresponsive=False),),
        )

    monkeypatch.setattr(backends, "probe_bsk", fake_probe)

    probes = await probe_backends(solver_url="http://127.0.0.1:8191", bsk_path="/opt/bsk")

    assert [probe.backend for probe in probes] == [
        BrowserBackendName.PATCHRIGHT,
        BrowserBackendName.CAMOUFOX,
        BrowserBackendName.BSK,
        BrowserBackendName.SOLVER,
    ]
    by_name = {probe.backend: probe for probe in probes}
    assert by_name[BrowserBackendName.PATCHRIGHT].available
    assert not by_name[BrowserBackendName.CAMOUFOX].available
    assert "camoufox" in (by_name[BrowserBackendName.CAMOUFOX].detail or "")
    assert by_name[BrowserBackendName.BSK].version == "0.3.3"
    assert by_name[BrowserBackendName.BSK].browsers[0].name == "edge"
    assert by_name[BrowserBackendName.SOLVER].available


@pytest.mark.asyncio
async def test_probe_backends_survives_missing_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "module_available", lambda module: False)
    monkeypatch.setattr(backends.httpx2, "AsyncClient", _OkClient)

    async def fake_probe(path: str | None) -> BskStatus:
        return BskStatus(available=False, detail="未找到 bsk CLI, 请先安装 BrowserSkill 并连接浏览器扩展")

    monkeypatch.setattr(backends, "probe_bsk", fake_probe)

    probes = await probe_backends(solver_url="http://127.0.0.1:8191")

    assert all(not probe.available for probe in probes if probe.backend is not BrowserBackendName.SOLVER)
    assert (probes[2].detail or "").startswith("未找到 bsk CLI")


def test_module_available_is_false_for_missing_module() -> None:
    assert backends.module_available("amane") is True
    assert backends.module_available("definitely_missing_module_xyz") is False
