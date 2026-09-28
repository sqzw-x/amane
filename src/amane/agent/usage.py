"""回合用量的对外形状: 回放行与 AG-UI 事件共用."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RunUsage, UsageBase


class TurnTokenUsage(BaseModel):
    """`input` 是非缓存输入 (总量减去 cache_read/cache_write). pydantic-ai 的 `input_tokens` 含缓存, 此处拆开.

    字段不给默认值: 回放行里的用量总是全字段, 前端因此可以直接参与算术.
    """

    input: int
    cache_read: int
    cache_write: int
    output: int
    requests: int


class RequestTokenUsage(BaseModel):
    """单次模型请求的用量. `duration_ms` 是请求发出到响应收到的本地时间差, 不含工具执行."""

    input: int
    cache_read: int
    cache_write: int
    output: int
    duration_ms: int | None = None


def _split_usage(usage: UsageBase) -> tuple[int, int, int, int]:
    cache_read = usage.cache_read_tokens
    cache_write = usage.cache_write_tokens
    return (
        max(0, usage.input_tokens - cache_read - cache_write),
        cache_read,
        cache_write,
        usage.output_tokens,
    )


def turn_usage_from_run(usage: RunUsage) -> TurnTokenUsage:
    input_tokens, cache_read, cache_write, output = _split_usage(usage)
    return TurnTokenUsage(
        input=input_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        output=output,
        requests=usage.requests,
    )


def request_usage(response: ModelResponse, sent_at: datetime | None) -> RequestTokenUsage:
    """单次模型请求的用量; `sent_at` 是这次请求发出的时刻, 缺失时只少 `duration_ms`."""
    input_tokens, cache_read, cache_write, output = _split_usage(response.usage)
    duration_ms = int((response.timestamp - sent_at).total_seconds() * 1000) if sent_at is not None else None
    return RequestTokenUsage(
        input=input_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        output=output,
        duration_ms=duration_ms,
    )
