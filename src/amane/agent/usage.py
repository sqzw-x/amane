"""回合用量的对外形状: 回放行与 AG-UI 事件共用."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic_ai import AgentRunResult
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
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
    """单次模型请求的用量. `duration_ms` 是请求发出到响应收到的本地时间差, 不含工具执行.

    `after_tool_call` 是该请求最后一次工具调用的 id: 页面据此把用量印在这次请求产出的内容之后;
    请求没有工具调用时为空, 用量落在消息末尾.
    """

    input: int
    cache_read: int
    cache_write: int
    output: int
    duration_ms: int | None = None
    after_tool_call: str | None = None


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


def request_usages_from_messages(messages: Sequence[ModelMessage]) -> list[RequestTokenUsage]:
    """按发生顺序取每次模型请求的用量; 请求缺时间戳时 `duration_ms` 为 None."""
    usages: list[RequestTokenUsage] = []
    sent_at: datetime | None = None
    for message in messages:
        if isinstance(message, ModelRequest):
            sent_at = message.timestamp
        elif isinstance(message, ModelResponse):
            input_tokens, cache_read, cache_write, output = _split_usage(message.usage)
            duration_ms = int((message.timestamp - sent_at).total_seconds() * 1000) if sent_at is not None else None
            usages.append(
                RequestTokenUsage(
                    input=input_tokens,
                    cache_read=cache_read,
                    cache_write=cache_write,
                    output=output,
                    duration_ms=duration_ms,
                    after_tool_call=_last_tool_call_id(message),
                )
            )
            sent_at = None
    return usages


def _last_tool_call_id(response: ModelResponse) -> str | None:
    """一次响应可以并发调多个工具; 用量挂在最后一个之后."""
    ids = [part.tool_call_id for part in response.parts if isinstance(part, ToolCallPart)]
    return ids[-1] if ids else None


def request_usages_from_run(result: AgentRunResult[Any]) -> list[RequestTokenUsage]:
    """本轮新产生的请求用量.

    首个请求常复用 `message_history` 末尾的那条 (用户输入即那条请求), `new_messages()` 会把它当既有
    上下文排除, 因此配对要在整段历史上做, 再按本轮新增的响应条数取末尾若干条.
    """
    usages = request_usages_from_messages(result.all_messages())
    responses = sum(1 for message in result.new_messages() if isinstance(message, ModelResponse))
    return usages[-responses:] if responses else []
