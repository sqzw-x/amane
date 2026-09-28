"""回合用量的对外形状: 回放行与 AG-UI 事件共用."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai.usage import RunUsage


class TurnTokenUsage(BaseModel):
    """`input` 是非缓存输入 (总量减去 cache_read/cache_write). pydantic-ai 的 `input_tokens` 含缓存, 此处拆开."""

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    requests: int = 0


def turn_usage_from_run(usage: RunUsage) -> TurnTokenUsage:
    cache_read = usage.cache_read_tokens
    cache_write = usage.cache_write_tokens
    return TurnTokenUsage(
        input=max(0, usage.input_tokens - cache_read - cache_write),
        cache_read=cache_read,
        cache_write=cache_write,
        output=usage.output_tokens,
        requests=usage.requests,
    )
