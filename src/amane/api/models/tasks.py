from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ...db import Repository, TaskStatus, TaskType
from ...handlers import (
    CacheKind,
    CleanupPayload,
    OrganizePayload,
    R18ImportPayload,
    RefreshPayload,
    RescrapePayload,
    ScrapePayload,
    UpscalePayload,
)
from ...parsing import ContentType, infer_content_type, parse_file_info


class TaskChildStatusCounts(BaseModel):
    """直接后继按状态计数. 四字段之和等于 child_count."""

    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


class TaskResponse(BaseModel):
    id: int
    type: TaskType
    status: TaskStatus
    title: str | None = None
    """展示用标题 (scrape→番号, actor_scrape→演员名, refresh/organize→库名)."""
    payload: dict = Field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    log_file: str | None = None
    retries: int = 0
    priority: int = 0
    root_task_id: int | None = None
    """链根任务 id (根任务指向自己; 裸任务为 None). 前端据此判断是否顶级节点."""
    child_count: int = 0
    """直接后继子任务数 (TaskLink 出边). 树节点是否可展开看这个."""
    child_status: TaskChildStatusCounts = Field(default_factory=TaskChildStatusCounts)
    """直接后继的状态分布; 折叠节点据此标失败/运行数, 不必展开整层."""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskChildResponse(TaskResponse):
    """树展开用的子任务: 比 TaskResponse 多一条出边的 key."""

    link_key: str
    """父节点内后继语义键 (如 scrape:{media_file_id})."""


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int


class TaskChildListResponse(BaseModel):
    items: list[TaskChildResponse]
    total: int
    """出边总数, 不受本页 limit/offset 截断."""


class TaskBatchAction(StrEnum):
    CANCEL = "cancel"
    DELETE = "delete"
    RETRY = "retry"


class TaskBatchRequest(BaseModel):
    action: TaskBatchAction
    task_ids: list[int] | None = Field(default=None, min_length=1)
    """按 ID 操作; 与 status/type 互斥."""
    status: list[TaskStatus] | None = Field(default=None, min_length=1)
    """与列表查询同形; 未传则不限. 与 task_ids 互斥."""
    type: list[TaskType] | None = Field(default=None, min_length=1)
    """与列表查询同形; 未传则不限. 与 task_ids 互斥."""

    @model_validator(mode="after")
    def _exclusive_scope(self) -> Self:
        if self.task_ids is not None and (self.status is not None or self.type is not None):
            raise ValueError("task_ids 与 status/type 不能同时指定")
        return self


class TaskBatchResponse(BaseModel):
    affected: int = 0
    skipped: int = 0
    missing: int = 0
    submitted: int = 0
    task_ids: list[int] = Field(default_factory=list)
    """retry 新建任务的 id; 其它 action 为空."""


class TaskWorkerResponse(BaseModel):
    paused: bool


class ScrapeRequest(BaseModel):
    number: str | None = Field(default=None)
    media_id: int | None = Field(
        default=None,
        description="MediaFile ID. 通常仅用于内部提交任务，手动提交无需指定",
    )
    content_type: ContentType | None = Field(
        default=None,
        description="内容类型; 未给出时: 仅 media_id 按文件路径推断, 有 number 时按番号推断",
    )
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 DB per-site 快照; trans: 复用译文). 空集 = 全部强制刷新",
    )

    @field_validator("number", mode="before")
    @classmethod
    def _normalize_number(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.number is None and self.media_id is None:
            raise ValueError("Either 'number' or 'media_id' must be provided")
        return self

    async def resolve(self, repo: Repository) -> ScrapePayload:
        """解析 number/media_id, 返回类型化的 ScrapePayload. media 不存在时抛 HTTPException(404)."""
        if self.media_id is not None:
            media = await repo.get_media_file(self.media_id)
            if media is None:
                raise HTTPException(status_code=404, detail=f"Media file {self.media_id} not found")
            if self.number is not None:
                return ScrapePayload(
                    number=self.number,
                    content_type=self.content_type or infer_content_type(self.number),
                    media_file_id=self.media_id,
                    use_cache=self.use_cache,
                )
            parsed = parse_file_info(media.path)
            assert parsed.number is not None
            return ScrapePayload(
                number=parsed.number,
                content_type=self.content_type or parsed.content_type,
                media_file_id=self.media_id,
                use_cache=self.use_cache,
            )
        assert self.number is not None
        return ScrapePayload(
            number=self.number,
            content_type=self.content_type or infer_content_type(self.number),
            use_cache=self.use_cache,
        )


class RefreshSubmission(RefreshPayload):
    type: Literal["refresh"]


class OrganizeSubmission(OrganizePayload):
    type: Literal["organize"]


class ScrapeSubmission(ScrapeRequest):
    type: Literal["scrape"]


class CleanupSubmission(CleanupPayload):
    type: Literal["cleanup"]


class UpscaleSubmission(UpscalePayload):
    type: Literal["upscale"]


class R18ImportSubmission(R18ImportPayload):
    type: Literal["r18_import"]


class RescrapeSubmission(RescrapePayload):
    type: Literal["rescrape"]


class ActorScrapeSubmission(BaseModel):
    type: Literal["actor_scrape"]
    actor_id: int = Field(description="Actor 实体 ID")
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 Actor.raw; trans: 预留译文). 空集 = 全部强制刷新",
    )


TaskSubmission = Annotated[
    RefreshSubmission
    | OrganizeSubmission
    | ScrapeSubmission
    | CleanupSubmission
    | UpscaleSubmission
    | R18ImportSubmission
    | ActorScrapeSubmission
    | RescrapeSubmission,
    Field(discriminator="type"),
]
"""即时任务提交体: 由 ``type`` 字段判别派发到对应的 handler."""


RoutineSubmission = Annotated[
    CleanupSubmission | UpscaleSubmission | R18ImportSubmission | RescrapeSubmission,
    Field(discriminator="type"),
]
"""定时任务提交体."""
