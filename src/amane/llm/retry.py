"""助理路径的传输层重试.

上游网络抖动很常见 (超时、连接中断). 助理对话是交互式的, 一次失败就让整轮报废太亏, 因此在传输层重试:
失败的那次请求整个重发, 既不产生重复回放行, 也不用重跑工具. 重试次数不设上限, 由用户停止回合终止.

只区分初始值与上限: 倍数与抖动为固定值, 不值得各自开一个配置项.

传输层在模型构造时定型 (随配置重建), 而重试发生在某个具体回合里; 两者靠 ContextVar 相接:
回合进入时挂上出口, 传输层每次重试前读一次. 没有回合在跑时 (如标题生成) 不转发任何进展.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token

import httpx2
import tenacity
from pydantic_ai.retries import AsyncHTTPX2TenacityTransport, RetryConfig

_MULTIPLIER = 2.0
"""退避倍数."""

_JITTER_S = 1.0
"""抖动上限: 多个会话同时失败时不至于同一刻一起重试."""

Sink = Callable[[int, float, str], Awaitable[None]]
"""收到一次重试: (第几次重试, 退避秒数, 失败原因)."""

_sink: ContextVar[Sink | None] = ContextVar("agent_retry_sink", default=None)


@contextmanager
def retry_sink(sink: Sink) -> Generator[None]:
    """回合内挂上出口; 退出时恢复上一层, 嵌套回合互不串扰."""
    token: Token[Sink | None] = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


async def _before_sleep(state: tenacity.RetryCallState) -> None:
    sink = _sink.get()
    if sink is None:
        return
    outcome = state.outcome
    reason = str(outcome.exception()) if outcome is not None and outcome.failed else "unknown"
    await sink(state.attempt_number, state.upcoming_sleep, reason)


def build_retry_transport(
    initial_s: float, max_s: float, wrapped: httpx2.AsyncBaseTransport | None = None
) -> AsyncHTTPX2TenacityTransport:
    """构造带退避重试的传输层.

    任何失败都重试, 不按状态码分类: 中转站的状态码不规范, 分类会把本该重试的判成永久失败.
    用户取消走 CancelledError, 它不在重试范围内, 停止回合因此仍然即时生效.
    """
    config: RetryConfig = {
        "retry": tenacity.retry_if_not_exception_type(asyncio.CancelledError),
        # 退避 = min(max_s, 起步 * 2**n + 随机(0, 抖动)): 倍数与抖动固定, 只让配置决定起步与上限
        "wait": tenacity.wait_exponential_jitter(
            multiplier=initial_s, max=max_s, exp_base=_MULTIPLIER, jitter=_JITTER_S
        ),
        "stop": tenacity.stop_never,
        "reraise": True,
        "before_sleep": _before_sleep,
    }
    return AsyncHTTPX2TenacityTransport(config, wrapped=wrapped)
