"""config.hot.json 直接复用 HotSettings dump, 不在此平行建模."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..net.errors import FailureReason

RECORD_VERSION: Literal[2] = 2
SECRETS_HOT_FILENAME = ".secrets.hot.json"
REDACTION_PLACEHOLDER = "***"


class CaptureReason(StrEnum):
    FAILURE = "failure"
    DEBUG_FLAG = "debug_flag"
    NONE = "none"


class RecordManifest(BaseModel):
    record_version: Literal[2] = RECORD_VERSION
    amane_version: str
    created_at: datetime
    task_id: int
    redacted: bool
    http_captured: bool
    capture_reason: CaptureReason


class TaskSnapshot(BaseModel):
    id: int
    type: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    priority: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SiteOutcomeKind(StrEnum):
    OK = "ok"
    FAILED = "failed"
    CACHE_HIT = "cache_hit"


class SiteOutcomeRecord(BaseModel):
    """由 Recorder.record_site_outcome 唯一写入."""

    site: str
    outcome: SiteOutcomeKind
    reason: FailureReason | None = None
    http_status: int | None = None
    """reason=http_error 时必有."""
    detail: str | None = None
    """展示用, 不解析."""
    detail_truncated: bool = False
    """结果里的 detail 是否被截断; 记录文件里始终是完整文本."""


class OrganizeConflictReason(StrEnum):
    """整理未处理的原因."""

    target_exists = "target_exists"
    """目标路径已被另一个文件占用."""
    subtitle_target_exists = "subtitle_target_exists"
    """字幕目标路径已被另一个文件占用."""


class OrganizeConflict(BaseModel):
    """一条未落盘的整理项: 源路径 + 被占用的目标路径."""

    path: str
    target: str
    reason: OrganizeConflictReason


OUTCOME_DETAIL_LIMIT = 200
"""任务结果里站点明细 `detail` 的截断长度; 完整文本只留在任务记录中."""


class TaskSummary(BaseModel):
    """只保留 task.json / http/ 无法直接表达的聚合信息."""

    eligible_sites: list[str] = Field(default_factory=list)
    """content_routes 下有资格的站点 (未必实际请求)."""
    sites_queried: list[str] = Field(default_factory=list)
    outcomes: dict[str, SiteOutcomeRecord] = Field(default_factory=dict)
    """key = cache_key; 仅 record_site_outcome 写入."""
    failed_sites: list[str] | None = None
    """仅 RECORD_VERSION 1 写入; 新记录留空, 读取时用于降级投影."""
    cache_hits: list[str] | None = None
    """仅 RECORD_VERSION 1 写入."""


class HttpExchangeMeta(BaseModel):
    seq: int
    site: str | None = None
    method: str
    url: str
    status: int | None = None
    error: str | None = None
    content_type: str | None = None
    body_file: str | None = None
    elapsed_ms: int | None = None
