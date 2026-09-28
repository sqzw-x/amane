"""回合用量换算测试."""

from __future__ import annotations

import pytest
from pydantic_ai.usage import RunUsage

from amane.agent.usage import turn_usage_from_run


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (RunUsage(input_tokens=100, cache_read_tokens=40, cache_write_tokens=10, output_tokens=20), (50, 40, 10, 20)),
        (RunUsage(input_tokens=10, output_tokens=5), (10, 0, 0, 5)),
        (RunUsage(input_tokens=5, cache_read_tokens=10, output_tokens=1), (0, 10, 0, 1)),
    ],
)
def test_turn_usage_from_run(run: RunUsage, expected: tuple[int, int, int, int]) -> None:
    u = turn_usage_from_run(run)
    assert (u.input, u.cache_read, u.cache_write, u.output) == expected


def test_turn_usage_includes_requests() -> None:
    u = turn_usage_from_run(RunUsage(input_tokens=10, output_tokens=2, requests=3))
    assert u.requests == 3
