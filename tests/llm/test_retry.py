"""传输层重试: 失败重发同一请求, 退避可配置, 取消不被吞掉. 全部用假传输驱动, 不触网."""

from __future__ import annotations

import asyncio

import httpx2
import pytest
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport

from amane.llm.retry import build_retry_transport, retry_sink


class _FlakyTransport(httpx2.AsyncBaseTransport):
    """前 n 次失败, 之后成功: 驱动传输层重试而不触网."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx2.ConnectError("Connection error.", request=request)
        return httpx2.Response(200, json={"ok": True}, request=request)


def _wrap(transport: httpx2.AsyncBaseTransport) -> AsyncHTTPX2TenacityTransport:
    """生产同一份退避配置, 把等待压到毫秒级."""
    return build_retry_transport(0.001, 0.002, wrapped=transport)


@pytest.mark.asyncio
async def test_retry_transport_retries_until_success() -> None:
    """失败的那次请求被整个重发, 成功即返回; 重试次数不设上限."""
    transport = _FlakyTransport(failures=2)
    async with httpx2.AsyncClient(transport=_wrap(transport)) as client:
        response = await client.post("http://example.test/v1/chat", content=b"{}")

    assert response.status_code == 200
    assert transport.calls == 3


@pytest.mark.asyncio
async def test_retry_transport_reports_each_attempt() -> None:
    """每次重试前回调一次: 第几次、退避多久、为什么失败."""
    seen: list[tuple[int, float, str]] = []

    async def on_retry(attempt: int, delay_s: float, reason: str) -> None:
        seen.append((attempt, delay_s, reason))

    transport = _FlakyTransport(failures=1)
    async with httpx2.AsyncClient(transport=_wrap(transport)) as client:
        with retry_sink(on_retry):
            await client.post("http://example.test/v1/chat", content=b"{}")

    assert [attempt for attempt, _, _ in seen] == [1]
    assert seen[0][1] > 0
    assert "Connection error." in seen[0][2]


@pytest.mark.asyncio
async def test_retry_transport_does_not_retry_cancel() -> None:
    """取消不在重试范围内: 用户停止后请求不得被重发."""
    transport = _FlakyTransport(failures=10_000)
    retrying = _wrap(transport)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=retrying) as client:
            await client.post("http://example.test/v1/chat", content=b"{}")

    task = asyncio.create_task(run())
    while transport.calls == 0:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
