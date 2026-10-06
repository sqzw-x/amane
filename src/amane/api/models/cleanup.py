"""清理清单的读模型: 面板只读, 删除仍经任务提交."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ...library import PlanEntryKind, PlanReason


class PlanNodeResponse(BaseModel):
    """树节点. ``path`` 库内为相对路径, 库外为绝对路径; 子节点按需再取."""

    path: str
    name: str
    kind: PlanEntryKind
    reason: PlanReason | None = None
    """仅条目节点有; 容器目录为 None."""
    size: int | None = None
    outside: bool = False
    hardlink: bool = False
    entry_count: int
    """子树内的条目数."""
    entry_bytes: int
    """子树内的条目体积, 同 inode 只算一次."""
    will_be_empty: bool = False
    """清单条目全部删除后该目录是否会空 (含子目录递归)."""
    has_children: bool = False
    children: list[PlanNodeResponse] | None = None


class PlanSummaryResponse(BaseModel):
    """面板一次取到状态、范围与顶层节点; ``exists`` 为假时其余字段无意义."""

    exists: bool
    plan_id: str | None = None
    created_at: datetime | None = None
    scope_path: str | None = None
    """非空表示本次清单只覆盖该子目录, 面板据此标注范围."""
    truncated: bool = False
    skipped_dirs: int = 0
    skipped_files: int = 0
    entry_count: int = 0
    entry_bytes: int = 0
    dir_count: int = 0
    scan_running: bool = False
    """该库是否有扫描无效文件任务在跑, 避免重复触发."""
    nodes: list[PlanNodeResponse] = []


class PlanNodesResponse(BaseModel):
    nodes: list[PlanNodeResponse] = []


class TrashSummaryResponse(BaseModel):
    """回收站历史内容: 展开即产出显式来源清单, 面板按同一套审查与删除处理."""

    exists: bool
    plan_id: str | None = None
    entry_count: int = 0
    entry_bytes: int = 0
    nodes: list[PlanNodeResponse] = []


class SelectionRequest(BaseModel):
    """由选中的媒体文件展开显式来源清单."""

    media_file_ids: list[int] = Field(min_length=1, description="选中的媒体文件 ID; 必须属于该库")
    include_work_dir: bool = Field(
        default=False, description="连同作品文件夹一起删除; 仅在该目录只含这一条媒体索引且不是库根时提供"
    )


class SelectionSummaryResponse(BaseModel):
    exists: bool
    plan_id: str | None = None
    entry_count: int = 0
    entry_bytes: int = 0
    nodes: list[PlanNodeResponse] = []
    notices: list[str] = []
    """未能纳入的部分与原因 (例如作品目录不满足整目录删除的条件)."""
