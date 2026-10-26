"""Windows 原生监控: 目录句柄只由 emitter 线程关闭.

watchdog 6.0.0 的 ``WindowsApiEmitter`` 在 ``stop()`` 里由调用线程关闭目录句柄, 而 emitter
线程此时可能仍在该句柄上执行 ``ReadDirectoryChangesW``. 句柄被并发关闭后, 该线程在已失效的
句柄上退出, 表现为 access violation (整个进程崩溃) 或停止流程不返回.

目录句柄的关闭因此只在 emitter 线程执行: 停止方反复 ``CancelIoEx`` 直到该线程退出. 必须重复
—— 单次取消可能早于线程进入读取而不产生效果, 读取随后仍然阻塞. 本模块依赖 ``_whandle`` 与
``stop()`` 的调用时序, 因此 ``watchdog`` 上限钉在 7 之前; 上游发版修好该竞态后删除本模块.
"""

from __future__ import annotations

import contextlib
import threading
import time

from watchdog.observers.api import DEFAULT_OBSERVER_TIMEOUT, BaseObserver
from watchdog.observers.read_directory_changes import WindowsApiEmitter
from watchdog.observers.winapi import CancelIoEx, close_directory_handle

_CANCEL_INTERVAL_SECONDS = 0.01
_CANCEL_DEADLINE_SECONDS = 5.0


class _HandleOwnerEmitter(WindowsApiEmitter):
    def _close_handle(self) -> None:
        handle, self._whandle = self._whandle, None
        if handle:
            close_directory_handle(handle)

    def on_thread_stop(self) -> None:
        # 合成"根目录已删除"事件后 watchdog 由 emitter 线程自身 stop(), 此时不能等自己.
        if threading.current_thread() is self:
            return
        handle = self._whandle
        deadline = time.monotonic() + _CANCEL_DEADLINE_SECONDS
        while handle and self.is_alive() and time.monotonic() < deadline:
            with contextlib.suppress(OSError):
                CancelIoEx(handle, None)
            self.join(timeout=_CANCEL_INTERVAL_SECONDS)
        if self.ident is None:
            self._close_handle()  # 线程从未启动, 不会有人再读该句柄

    def run(self) -> None:
        try:
            super().run()
        finally:
            self._close_handle()


class WindowsObserver(BaseObserver):
    """与 ``watchdog.observers.Observer`` 同构, 只替换 emitter 的停止语义."""

    def __init__(self, *, timeout: float = DEFAULT_OBSERVER_TIMEOUT) -> None:
        super().__init__(_HandleOwnerEmitter, timeout=timeout)
