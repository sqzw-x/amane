"""任务与定时任务入参的按需披露.

`TaskSubmission` (9 型) 与 `RoutineSubmission` (4 型) 的联合体远大于其余工具定义, 随工具签名内联会长期
占用每次请求的固定前缀. 因此分两级披露: 工具的 `submission_type` 参数枚举可用类型, 字段定义由模型按需
调用取得; 校验失败时同一份字段定义随错误返回. schema 与校验共用同一组模型, 不存在第二份定义.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..api.models.tasks import (
    ActorScrapeSubmission,
    CleanupSubmission,
    OrganizeSubmission,
    R18ImportSubmission,
    RefreshSubmission,
    RescrapeSubmission,
    RoutineSubmission,
    ScrapeSubmission,
    TaskSubmission,
    TrashSubmission,
    UpscaleSubmission,
)

TaskSubmissionType = Literal[
    "refresh",
    "organize",
    "trash",
    "scrape",
    "cleanup",
    "upscale",
    "r18_import",
    "actor_scrape",
    "rescrape",
]
RoutineSubmissionType = Literal["cleanup", "upscale", "r18_import", "rescrape"]

_TASK_MEMBERS: dict[str, type[BaseModel]] = {
    "refresh": RefreshSubmission,
    "organize": OrganizeSubmission,
    "trash": TrashSubmission,
    "scrape": ScrapeSubmission,
    "cleanup": CleanupSubmission,
    "upscale": UpscaleSubmission,
    "r18_import": R18ImportSubmission,
    "actor_scrape": ActorScrapeSubmission,
    "rescrape": RescrapeSubmission,
}
_ROUTINE_MEMBERS: dict[str, type[BaseModel]] = {
    "cleanup": CleanupSubmission,
    "upscale": UpscaleSubmission,
    "r18_import": R18ImportSubmission,
    "rescrape": RescrapeSubmission,
}


@dataclass(frozen=True)
class SubmissionSpec[T]:
    """一类入参: 联合体校验入口, 以及按类型取字段定义."""

    union: TypeAdapter[T]
    members: dict[str, type[BaseModel]]

    def schema(self, submission_type: str) -> dict[str, Any]:
        """该类型的字段定义, 与提交时的校验来源相同."""
        return TypeAdapter(self.members[submission_type]).json_schema()

    def validate(self, data: dict[str, Any]) -> T:
        """校验入参, 保留联合体类型; 失败时抛出 ``ValidationError``."""
        return self.union.validate_python(data)

    def error(self, data: dict[str, Any], exc: ValidationError) -> dict[str, Any]:
        """把校验失败整理为工具返回值: 出错字段 (最多三条), 可用类型, 以及类型已知时的字段定义."""
        details = _error_details(exc)
        out: dict[str, Any] = {
            "error": f"参数无效: {details[0]}",
            "errors": details[:3],
            "types": sorted(self.members),
        }
        declared = data.get("type")
        if isinstance(declared, str) and declared in self.members:
            out["schema"] = self.schema(declared)
        return out


def _error_details(exc: ValidationError) -> list[str]:
    """逐条列出 ``loc`` 与原因; 一次提交多处出错时模型无需逐轮修正."""
    details: list[str] = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item["loc"]) or "(root)"
        text = f"{loc}: {item['msg']}"
        if text not in details:
            details.append(text)
    return details


TASK_SUBMISSION: SubmissionSpec[TaskSubmission] = SubmissionSpec(
    union=TypeAdapter(TaskSubmission), members=_TASK_MEMBERS
)
ROUTINE_SUBMISSION: SubmissionSpec[RoutineSubmission] = SubmissionSpec(
    union=TypeAdapter(RoutineSubmission), members=_ROUTINE_MEMBERS
)
