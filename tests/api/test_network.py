"""/api/network: 来源连通性探测."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from amane.net.errors import FailureKind, RequestError, RequestFailure

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from httpx2 import AsyncClient as HttpxClient


class _Resp:
    """只提供探测读取的字段."""

    def __init__(self, text: str, status: int = 200) -> None:
        self.status_code = status
        self.url = "https://stub.example.test/"
        self.headers: dict[str, str] = {}
        self._text = text

    @property
    def text(self) -> str:
        return self._text


def _stub_transport(app: FastAPI, monkeypatch: pytest.MonkeyPatch, handler: Callable[[str], _Resp]) -> list[str]:
    """替换唯一出站通道: 记录被探测的地址, 不真发网络."""
    urls: list[str] = []

    async def request(method: str, url: str, **kwargs: Any) -> _Resp:
        urls.append(url)
        return handler(url)

    monkeypatch.setattr(app.state.runtime.web_client, "request", request)
    return urls


def _configured_ids(app: FastAPI) -> list[str]:
    hot = app.state.runtime.config.hot
    ids = [site for chain in hot.scraping.content_routes.values() for site in chain]
    ids += [str(site) for site in hot.actor_scraping.profile_sites]
    ids += [str(site) for site in hot.actor_scraping.image_sites]
    return list(dict.fromkeys(ids))


@pytest.mark.asyncio(loop_scope="function")
async def test_full_sweep_covers_configured_sources(client: HttpxClient, app: FastAPI, monkeypatch) -> None:
    urls = _stub_transport(app, monkeypatch, lambda url: _Resp("<html>ok</html>"))

    resp = await client.post("network/check")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["source_id"] for item in items] == _configured_ids(app)
    # 假通道永远成功, 所以一次 failed 都不该出现; skipped 是来源自己声明不探测 (离线镜像 / 无凭据).
    assert "failed" not in {item["status"] for item in items}
    assert {item["url"] for item in items if item["status"] == "ok"} == set(urls)
    # 只有真探测过的条目才有耗时; 未探测的报告 None, 界面据此显示「—」而不是 0 ms.
    assert all(isinstance(item["elapsed_ms"], int) for item in items if item["status"] == "ok")
    assert all(item["elapsed_ms"] is None for item in items if item["status"] == "skipped")
    assert {item["source_id"]: item["kind"] for item in items}["gfriends"] == "actor"


@pytest.mark.asyncio(loop_scope="function")
async def test_single_source_retry_probes_only_that_source(client: HttpxClient, app: FastAPI, monkeypatch) -> None:
    urls = _stub_transport(app, monkeypatch, lambda url: _Resp("<html>ok</html>"))

    resp = await client.post("network/check", json={"source_ids": ["javdb"]})

    assert resp.status_code == 200
    (item,) = resp.json()["items"]
    assert item["source_id"] == "javdb"
    assert urls == [item["url"]]


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_body_falls_back_to_configured_scope(client: HttpxClient, app: FastAPI, monkeypatch) -> None:
    _stub_transport(app, monkeypatch, lambda url: _Resp("<html>ok</html>"))

    resp = await client.post("network/check", json={})

    assert resp.status_code == 200
    assert [item["source_id"] for item in resp.json()["items"]] == _configured_ids(app)


@pytest.mark.asyncio(loop_scope="function")
async def test_unknown_source_is_skipped(client: HttpxClient, app: FastAPI, monkeypatch) -> None:
    _stub_transport(app, monkeypatch, lambda url: _Resp("<html>ok</html>"))

    resp = await client.post("network/check", json={"source_ids": ["nope", "javdb"]})

    assert resp.status_code == 200
    by_id = {item["source_id"]: item for item in resp.json()["items"]}
    assert by_id["nope"]["status"] == "skipped"
    assert by_id["nope"]["reason"] is None
    assert by_id["nope"]["detail"] == "来源不存在或未启用"
    assert by_id["nope"]["elapsed_ms"] is None
    assert by_id["javdb"]["status"] == "ok"


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("text", "status_code", "reason"),
    [
        ("<html>slow</html>", 200, "network"),
        ("This content is not available in your region", 200, "geo_restricted"),
    ],
)
async def test_failure_stays_200_and_reports_reason(
    client: HttpxClient, app: FastAPI, monkeypatch, text: str, status_code: int, reason: str
) -> None:
    def handler(url: str) -> _Resp:
        if reason == "network":
            raise RequestError(url, RequestFailure(kind=FailureKind.CURL, message="curl error"))
        return _Resp(text, status=status_code)

    _stub_transport(app, monkeypatch, handler)

    resp = await client.post("network/check", json={"source_ids": ["javdb"]})

    assert resp.status_code == 200
    (item,) = resp.json()["items"]
    assert (item["source_id"], item["status"], item["reason"]) == ("javdb", "failed", reason)
    assert item["http_status"] == (None if reason == "network" else status_code)
