"""任务与定时任务入参 schema 的按需披露测试."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from amane.agent.payload_schema import ROUTINE_SUBMISSION, TASK_SUBMISSION, SubmissionSpec
from amane.agent.schedule_ops import build_schedule_ops_capability
from amane.agent.task_ops import build_task_ops_capability
from amane.api.models.tasks import ScrapeSubmission, TaskSubmission


def _tool_fn(cap: Any, name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    toolset = cap.get_toolset()
    assert toolset is not None
    return cast(Callable[..., Awaitable[dict[str, Any]]], toolset.tools[name].function)


def _ctx() -> Any:
    """直调工具函数时占位: 这些用例只走 schema 披露与校验失败两条路径, 不读 deps."""
    return SimpleNamespace(deps=SimpleNamespace())


def _union_types(union: Any) -> set[str]:
    return set(TypeAdapter(union).json_schema()["discriminator"]["mapping"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("builder", "tool_name", "spec"),
    [
        (build_task_ops_capability, "get_task_submission_schema", TASK_SUBMISSION),
        (build_schedule_ops_capability, "get_routine_submission_schema", ROUTINE_SUBMISSION),
    ],
)
async def test_schema_tool_covers_members(builder: Callable[[], Any], tool_name: str, spec: Any) -> None:
    """参数枚举必须与准入成员表一致: 缺一个则模型取不到形状, 多一个则越权可提交."""
    cap = builder()
    toolset = cap.get_toolset()
    assert toolset is not None
    enum = set(toolset.tools[tool_name].function_schema.json_schema["properties"]["submission_type"]["enum"])
    assert enum == set(spec.members)

    for submission_type in sorted(enum):
        out = await _tool_fn(cap, tool_name)(_ctx(), submission_type=submission_type)
        # 返回的只含该类型自身字段, 且 type 固定为它
        assert out["properties"]["type"]["const"] == submission_type


def test_delete_not_admitted_to_agent() -> None:
    """删除是面板专用入口: 联合体里有它, 助理的成员表里没有."""
    assert "delete" in _union_types(TaskSubmission)
    assert "delete" not in TASK_SUBMISSION.members


def test_admitted_types_match_union() -> None:
    """成员表里的每个类型都必须在联合体里: 名字写错时枚举有它, 提交时的校验却必失败."""
    assert set(TASK_SUBMISSION.members) <= _union_types(TaskSubmission)
    assert "emby_sync" in TASK_SUBMISSION.members


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("submission", "locs"),
    [
        (
            {"type": "rescrape", "limit": "many", "min_age_days": "lots", "targets": "nope"},
            ["rescrape.limit", "rescrape.min_age_days", "rescrape.targets"],
        ),
        (
            {"type": "scrape", "number": [1], "media_id": "x", "content_type": "nope", "use_cache": "nope"},
            ["scrape.number", "scrape.media_id", "scrape.content_type"],
        ),
    ],
)
async def test_submit_task_error_lists_bad_fields(submission: dict[str, Any], locs: list[str]) -> None:
    """一次提交多处出错时回执列出出错字段 (截取前三条), 模型无需逐轮修正."""
    out = await _tool_fn(build_task_ops_capability(), "submit_task")(_ctx(), submission=submission)
    assert [detail.split(":")[0] for detail in out["errors"]] == locs
    assert out["error"].startswith(f"参数无效: {locs[0]}:")


@pytest.mark.parametrize(
    ("submission", "admitted"),
    [
        ({"type": "scrape", "number": "ABC-001"}, True),
        ({"type": "cleanup"}, False),
        ({"type": "nope"}, False),
        ({}, False),
    ],
)
def test_members_gate_admission(submission: dict[str, Any], admitted: bool) -> None:
    """成员表是准入真值: 联合体里有的类型, 未列入成员表同样拒绝."""
    spec: SubmissionSpec[TaskSubmission] = SubmissionSpec(
        union=TypeAdapter(TaskSubmission), members={"scrape": ScrapeSubmission}
    )

    if admitted:
        assert spec.validate(submission).type == "scrape"
        return
    with pytest.raises(ValidationError) as excinfo:
        spec.validate(submission)
    detail = spec.error(submission, excinfo.value)
    assert detail["types"] == ["scrape"]
    assert "可用类型: scrape" in detail["error"]
