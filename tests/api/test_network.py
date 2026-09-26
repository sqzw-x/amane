"""/api/network: 来源连通性探测的接线与 JSON 形状."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

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


def _ok(url: str) -> _Resp:
    return _Resp("<html>ok</html>")


def _curl_error(url: str) -> NoReturn:
    raise RequestError(url, RequestFailure(kind=FailureKind.CURL, message="curl error"))


def _geo_restricted(url: str) -> _Resp:
    return _Resp("<html>not available in your region</html>")


def _stub_transport(app: FastAPI, monkeypatch: pytest.MonkeyPatch, handler: Callable[[str], _Resp]) -> list[str]:
    """替换唯一出站通道: 记录被探测的地址, 不真发网络."""
    urls: list[str] = []

    async def request(method: str, url: str, **kwargs: Any) -> _Resp:
        urls.append(url)
        return handler(url)

    monkeypatch.setattr(app.state.runtime.web_client, "request", request)
    return urls


@pytest.mark.asyncio(loop_scope="function")
async def test_network_check(client: HttpxClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """请求体形状与响应 JSON 形状.

    探测范围, 判定与原因分类在各自实现层覆盖 (tests/crawlers/test_connectivity.py 与 tests/net);
    这里只钉住端点特有的部分: 始终 200, 逐来源条目字段, 以及未探测时哪些字段为空.
    """
    # 省略 body 与空对象走同一分支: 探测当前热配置真正会请求的全部来源.
    for body in (None, {}):
        urls = _stub_transport(app, monkeypatch, _ok)

        resp = await client.post("network/check", json=body)

        assert resp.status_code == 200
        items = resp.json()["items"]
        by_id = {item["source_id"]: item for item in items}
        assert len(items) == len(by_id)
        assert {"dmm", "javdb", "gfriends"} <= set(by_id)
        assert (by_id["dmm"]["kind"], by_id["gfriends"]["kind"]) == ("film", "actor")
        # 假通道恒成功, 因此不出现 failed; skipped 是来源自己声明不探测 (离线镜像 / 缺凭据).
        assert "failed" not in {item["status"] for item in items}
        assert {item["url"] for item in items if item["status"] == "ok"} == set(urls)
        # 只有真探测过的条目才有耗时: 界面据此显示「—」而不是 0 ms.
        assert all(item["elapsed_ms"] is not None for item in items if item["status"] == "ok")
        assert all(item["elapsed_ms"] is None for item in items if item["status"] != "ok")
        # 结论与结构化原因一一对应: 失败带 reason, 未探测带 skip_reason, 其余两者皆空.
        for item in items:
            assert (item["reason"] is not None) == (item["status"] == "failed")
            assert (item["skip_reason"] is not None) == (item["status"] == "skipped")

    # 单点重试: 只探传入的那一个来源.
    urls = _stub_transport(app, monkeypatch, _ok)

    resp = await client.post("network/check", json={"source_ids": ["javdb"]})

    assert resp.status_code == 200
    (item,) = resp.json()["items"]
    assert (item["source_id"], item["kind"], item["status"]) == ("javdb", "film", "ok")
    assert urls == [item["url"]]

    # 不存在的来源计入条目报 skipped, 不当作失败; 原因走枚举, 界面按它本地化.
    _stub_transport(app, monkeypatch, _ok)

    resp = await client.post("network/check", json={"source_ids": ["nope", "javdb"]})

    assert resp.status_code == 200
    by_id = {item["source_id"]: item for item in resp.json()["items"]}
    assert (
        by_id["nope"]["status"],
        by_id["nope"]["skip_reason"],
        by_id["nope"]["reason"],
        by_id["nope"]["elapsed_ms"],
    ) == ("skipped", "unknown_source", None, None)
    assert by_id["javdb"]["status"] == "ok"

    # 失败只进条目: 端点仍 200; 没走到 HTTP 的失败没有状态码.
    failure_cases: list[tuple[Callable[[str], _Resp], str, int | None]] = [
        (_curl_error, "network", None),
        (_geo_restricted, "geo_restricted", 200),
    ]
    for stub, reason, http_status in failure_cases:
        _stub_transport(app, monkeypatch, stub)

        resp = await client.post("network/check", json={"source_ids": ["javdb"]})

        assert resp.status_code == 200
        (item,) = resp.json()["items"]
        assert (item["status"], item["reason"], item["http_status"]) == ("failed", reason, http_status)
