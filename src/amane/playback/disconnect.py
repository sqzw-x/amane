"""客户端断开信号.

``Request.is_disconnected`` 以「立即取消」的方式探测 ``receive``, 只在断连消息已经等在通道里时
才返回 ``True``. 只要外层套了 ``starlette.middleware.base.BaseHTTPMiddleware`` (本项目的鉴权与
日志中间件都是), 它就永远返回 ``False``: 中间件包装出的 ``receive`` 必须真正挂起才会收到
``http.disconnect``, 被立刻取消的等待拿不到消息. 于是断连探测改为独立任务阻塞等待断开消息,
读循环只查标记.

后台任务还覆盖 ``send`` 被写阻塞的情形: 服务器在连接断开时释放写阻塞, 此时标记已经置位, 读循环
不会再提交下一次读取.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.types import Receive


class DisconnectSignal:
    """客户端是否已断开; 仅在 :meth:`watch` 期间由后台任务置位."""

    def __init__(self) -> None:
        self._disconnected = asyncio.Event()

    @property
    def disconnected(self) -> bool:
        return self._disconnected.is_set()

    @asynccontextmanager
    async def watch(self, receive: Receive) -> AsyncIterator[None]:
        """在响应生命周期内等待 ``http.disconnect``.

        ``receive`` 必须是响应拿到的那一个: 它可能已被中间件包装, 包装内的等待才是真正的等待.
        等待期间会消费掉请求正文消息 (播放请求没有正文).
        """
        task = asyncio.create_task(self._wait(receive))
        try:
            yield
        finally:
            task.cancel()
            # 只吞掉自己发起的取消: 调用方被取消时 CancelledError 会从 gather 抛出并继续上抛
            await asyncio.gather(task, return_exceptions=True)

    async def _wait(self, receive: Receive) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self._disconnected.set()
                return
