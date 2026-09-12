"""断连信号: 只有真正挂起等待 ``receive`` 才能观察到客户端离开."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from starlette.types import Message

from amane.playback.disconnect import DisconnectSignal


class _ScriptedReceive:
    """给出请求正文消息后挂起, 直到 ``disconnect()`` 被调用才报告断开."""

    def __init__(self) -> None:
        self.calls = 0
        self._disconnected = asyncio.Event()

    async def __call__(self) -> Message:
        self.calls += 1
        if self.calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnected.wait()
        return {"type": "http.disconnect"}

    def disconnect(self) -> None:
        self._disconnected.set()


async def _until(predicate: Callable[[], bool]) -> None:
    """等后台任务跑到断言点, 不依赖固定时长."""
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("条件未在超时前成立")


@pytest.mark.asyncio
async def test_watch_reports_disconnect_after_request_body() -> None:
    """请求正文消息不是断开; 之后异步到达的 ``http.disconnect`` 才置位."""
    receive = _ScriptedReceive()
    signal = DisconnectSignal()

    async with signal.watch(receive):
        assert signal.disconnected is False
        receive.disconnect()
        await _until(lambda: signal.disconnected)

    assert signal.disconnected is True


@pytest.mark.asyncio
async def test_watch_stops_waiting_on_exit() -> None:
    """退出时取消等待任务: 不再占用 ``receive``, 也不会误报断开."""
    receive = _ScriptedReceive()
    signal = DisconnectSignal()

    async with signal.watch(receive):
        await _until(lambda: receive.calls >= 2)

    calls = receive.calls
    await asyncio.sleep(0.02)

    assert receive.calls == calls
    assert signal.disconnected is False
