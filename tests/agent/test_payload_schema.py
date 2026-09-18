"""任务与定时任务入参 schema 的按需披露测试."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from amane.agent.schedule_ops import build_schedule_ops_capability
from amane.agent.task_ops import build_task_ops_capability
from amane.api.models.tasks import RoutineSubmission, TaskSubmission


def _tool_fn(cap: Any, name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    toolset = cap.get_toolset()
    assert toolset is not None
    return cast(Callable[..., Awaitable[dict[str, Any]]], toolset.tools[name].function)


def _union_types(union: Any) -> set[str]:
    return set(TypeAdapter(union).json_schema()["discriminator"]["mapping"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("builder", "tool_name", "union"),
    [
        (build_task_ops_capability, "get_task_submission_schema", TaskSubmission),
        (build_schedule_ops_capability, "get_routine_submission_schema", RoutineSubmission),
    ],
)
async def test_schema_tool_covers_union_types(builder: Callable[[], Any], tool_name: str, union: Any) -> None:
    """参数枚举必须与校验接受的类型一致: 缺一个则模型取不到形状, 多一个则拿到不存在的类型."""
    cap = builder()
    toolset = cap.get_toolset()
    assert toolset is not None
    enum = set(toolset.tools[tool_name].function_schema.json_schema["properties"]["submission_type"]["enum"])
    assert enum == _union_types(union)

    ctx = SimpleNamespace(deps=SimpleNamespace(persist_tool_trace=False))
    for submission_type in sorted(enum):
        out = await _tool_fn(cap, tool_name)(ctx, submission_type=submission_type)
        assert out["submission_type"] == submission_type
        # 返回的只含该类型自身字段, 且 type 固定为它
        assert out["schema"]["properties"]["type"]["const"] == submission_type
