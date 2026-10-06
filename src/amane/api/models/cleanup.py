"""清理清单的读模型: 面板只读, 删除仍经任务提交."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ...library import PlanEntryKind, PlanReason


class PlanNodeResponse(BaseModel):
    """树节点. ``path`` 库内为相对路径, 库外为绝对路径, 一律 `/` 分隔; 子节点按需再取."""

    path: str
    name: str
    kind: PlanEntryKind
    reason: PlanReason | None = None
    """仅条目节点有; 容器目录为 None."""
    size: int | None = None
    hardlink: bool = False
    entry_count: int
    """子树内的条目数."""
    entry_bytes: int
    """子树内的条目体积, 同 inode 只算一次."""
    will_be_empty: bool = False
    """清单条目全部删除后该目录是否会空 (含子目录递归)."""
    has_children: bool = False
    children: list[PlanNodeResponse] | None = None


class PlanNodePage(BaseModel):
    """一个目录的子节点切片. 面板只渲染 ``items``, 滚到底再按 ``offset`` 取下一页."""

    path: str
    """本页展开的目录: 库内相对路径, 库外为绝对路径, 一律 `/` 分隔, 空串为库根."""
    items: list[PlanNodeResponse]
    total: int
    """该目录的子节点总数, 与 ``items`` 的长度无关."""
    offset: int
    limit: int
    entry_count: int
    """该目录子树内的条目总数; 面板据此算选中量, 而不是把已加载的条目加起来."""
    entry_bytes: int


class PlanSummaryResponse(BaseModel):
    """面板入口: 状态与范围. ``exists`` 为假时其余字段无意义; 节点一律经分页接口另取."""

    exists: bool
    plan_id: str | None = None
    created_at: datetime | None = None
    scope_path: str | None = None
    """非空表示本次清单只覆盖该子目录, 面板据此标注范围."""
    truncated: bool = False
    dropped: int = 0
    """触顶后未纳入清单的候选数; 截断时面板据此提示还有多少没看到."""
    skipped_dirs: int = 0
    skipped_files: int = 0
    scan_running: bool = False
    """该库是否有扫描无效文件任务在跑, 避免重复触发."""
    last_scan_error: str | None = None
    """最近一次扫描的失败原因; 面板在无清单时据此显示错误."""


class TrashSummaryResponse(BaseModel):
    """回收站历史内容: 展开即产出显式来源清单, 面板套用同一套审查与删除."""

    exists: bool
    plan_id: str | None = None
    path: str | None = None
    """要展开的目录 (清单库根下的回收站), 交给分页接口."""
    truncated: bool = False
    dropped: int = 0


class SelectionRequest(BaseModel):
    """由选中的媒体文件展开显式来源清单."""

    media_file_ids: list[int] = Field(min_length=1, description="选中的媒体文件 ID; 必须属于该库")
    include_work_dir: bool = Field(
        default=False, description="连同作品文件夹一起删除; 仅在该目录只含这一条媒体索引且不是库根时提供"
    )


class SelectionSummaryResponse(BaseModel):
    """展开结果. 条目自库根展开 (库外产物挂在根下), 面板按分页接口读取."""

    exists: bool
    plan_id: str | None = None
    notices: list[str] = []
    """未能纳入的部分与原因 (例如作品目录不满足整目录删除的条件)."""
    truncated: bool = False
    dropped: int = 0
