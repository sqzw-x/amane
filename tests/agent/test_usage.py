"""回合用量换算测试."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage, RunUsage

from amane.agent.usage import RequestTokenUsage, request_usage, turn_usage_from_run


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


def _response(seconds: float, usage: RequestUsage) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="答")], usage=usage, timestamp=_START + timedelta(seconds=seconds))


@pytest.mark.parametrize(
    ("sent_at", "response", "expected"),
    [
        # 缓存部分从未缓存输入里拆出; 耗时取请求发出到响应收到的时间差
        (
            _START,
            _response(
                2.5, RequestUsage(input_tokens=100, cache_read_tokens=40, cache_write_tokens=10, output_tokens=20)
            ),
            RequestTokenUsage(input=50, cache_read=40, cache_write=10, output=20, duration_ms=2500),
        ),
        # 缓存读大于输入总量 → 非缓存输入夹到 0
        (
            _START,
            _response(0.4, RequestUsage(input_tokens=5, cache_read_tokens=10, output_tokens=1)),
            RequestTokenUsage(input=0, cache_read=10, cache_write=0, output=1, duration_ms=400),
        ),
        # 请求缺时间戳 → 只缺耗时, 用量照记
        (
            None,
            _response(1.0, RequestUsage(input_tokens=3, output_tokens=1)),
            RequestTokenUsage(input=3, cache_read=0, cache_write=0, output=1),
        ),
    ],
)
def test_request_usage(sent_at: datetime | None, response: ModelResponse, expected: RequestTokenUsage) -> None:
    assert request_usage(response, sent_at) == expected
