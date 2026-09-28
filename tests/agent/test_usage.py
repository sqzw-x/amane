"""回合用量换算测试."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage, RunUsage

from amane.agent.usage import (
    RequestTokenUsage,
    request_usages_from_messages,
    request_usages_from_run,
    turn_usage_from_run,
)


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (
            RunUsage(input_tokens=100, cache_read_tokens=40, cache_write_tokens=10, output_tokens=20),
            (50, 40, 10, 20, 0),
        ),
        (RunUsage(input_tokens=10, output_tokens=5, requests=3), (10, 0, 0, 5, 3)),
        (RunUsage(input_tokens=5, cache_read_tokens=10, output_tokens=1), (0, 10, 0, 1, 0)),
    ],
)
def test_turn_usage_from_run(run: RunUsage, expected: tuple[int, int, int, int, int]) -> None:
    u = turn_usage_from_run(run)
    assert (u.input, u.cache_read, u.cache_write, u.output, u.requests) == expected


_START = datetime(2026, 1, 1, tzinfo=UTC)


def _request(sent_at: datetime | None) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content="问")], timestamp=sent_at)


def _response(seconds: float, usage: RequestUsage, tool_calls: Sequence[str] = ()) -> ModelResponse:
    parts: list[ModelResponsePart] = [TextPart(content="答")]
    parts += [ToolCallPart(tool_name="sql_explore", args={}, tool_call_id=item) for item in tool_calls]
    return ModelResponse(parts=parts, usage=usage, timestamp=_START + timedelta(seconds=seconds))


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        # 两次请求: 各自拆出缓存部分, 耗时取请求发出到响应收到的时间差; 并发工具取最后一个作落点
        (
            [
                _request(_START),
                _response(
                    2.5,
                    RequestUsage(input_tokens=100, cache_read_tokens=40, cache_write_tokens=10, output_tokens=20),
                    tool_calls=("call-1", "call-2"),
                ),
                _request(_START + timedelta(seconds=3)),
                _response(3.2, RequestUsage(input_tokens=10, output_tokens=5)),
            ],
            [
                RequestTokenUsage(
                    input=50,
                    cache_read=40,
                    cache_write=10,
                    output=20,
                    duration_ms=2500,
                    after_tool_call="call-2",
                ),
                RequestTokenUsage(input=10, cache_read=0, cache_write=0, output=5, duration_ms=200),
            ],
        ),
        # 缓存读大于输入总量 → 非缓存输入夹到 0
        (
            [_request(_START), _response(0.4, RequestUsage(input_tokens=5, cache_read_tokens=10, output_tokens=1))],
            [RequestTokenUsage(input=0, cache_read=10, cache_write=0, output=1, duration_ms=400)],
        ),
        # 请求缺时间戳 → 只缺耗时, 用量照记
        (
            [_request(None), _response(1.0, RequestUsage(input_tokens=3, output_tokens=1))],
            [RequestTokenUsage(input=3, cache_read=0, cache_write=0, output=1)],
        ),
        # 中断在半途: 只有请求没有响应 → 不产生条目
        ([_request(_START)], []),
    ],
)
def test_request_usages_from_messages(messages: list[ModelMessage], expected: list[RequestTokenUsage]) -> None:
    assert request_usages_from_messages(messages) == expected


@pytest.mark.asyncio
async def test_request_usages_from_run_pairs_reused_first_request() -> None:
    """首个请求复用 message_history 末尾那条, 不在 new_messages 里, 耗时仍要配对成功."""
    agent: Agent[None, str] = Agent(TestModel(), output_type=str)

    @agent.tool_plain
    def ping() -> str:
        return "pong"

    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="你好")])]
    result = await agent.run(message_history=history)

    usages = request_usages_from_run(result)
    responses = [message for message in result.new_messages() if isinstance(message, ModelResponse)]
    assert len(usages) == len(responses) > 1
    assert all(item.duration_ms is not None for item in usages)
