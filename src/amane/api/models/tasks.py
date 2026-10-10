from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from ...db import Repository, TaskStatus, TaskType
from ...handlers import (
    ActorScrapeResult,
    CacheKind,
    CleanupPayload,
    CleanupResult,
    DeletePayload,
    DeleteResult,
    OrganizePayload,
    OrganizeResult,
    R18ImportPayload,
    R18ImportResult,
    RefreshPayload,
    RefreshResult,
    RescrapePayload,
    RescrapeResult,
    ScanInvalidPayload,
    ScanInvalidResult,
    ScrapePayload,
    ScrapeResult,
    UpscalePayload,
    UpscaleResult,
)
from ...parsing import EMPTY_NUMBER_RULES, ContentType, NumberRules, infer_content_type, parse_file_info


class TaskChildStatusCounts(BaseModel):
    """直接后继按状态计数. 四字段之和等于 child_count."""

    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


TaskResultPayload = Annotated[
    RefreshResult
    | OrganizeResult
    | ScanInvalidResult
    | DeleteResult
    | ScrapeResult
    | CleanupResult
    | UpscaleResult
    | R18ImportResult
    | ActorScrapeResult
    | RescrapeResult,
    Field(discriminator="type"),
]
"""任务结果按任务类型判别; 各成员自带 `type` 字面量, 前端据此收窄."""

_RESULT_ADAPTER: TypeAdapter[TaskResultPayload] = TypeAdapter(TaskResultPayload)


class TaskListItem(BaseModel):
    """列表与树: 不含 payload 与 result 等重字段, 展开时再取详情."""

    id: int
    type: TaskType
    status: TaskStatus
    title: str | None = None
    """scrape→番号, actor_scrape→演员名, refresh/organize/trash→库名."""
    error: str | None = None
    log_file: str | None = None
    retries: int = 0
    priority: int = 0
    root_task_id: int | None = None
    """根任务指向自己; 裸任务为 None. 前端据此判断是否顶级节点."""
    child_count: int = 0
    """TaskLink 出边数. 树节点是否可展开看这个."""
    child_status: TaskChildStatusCounts = Field(default_factory=TaskChildStatusCounts)
    """折叠节点据此标失败/运行数, 不必展开整层."""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskChildItem(TaskListItem):
    link_key: str
    """父节点内后继语义键 (如 scrape:{media_file_id})."""


class TaskResponse(TaskListItem):
    """详情: 额外带 payload 与按类型的 result."""

    payload: dict = Field(default_factory=dict)
    result: TaskResultPayload | None = None

    @field_validator("result", mode="before")
    @classmethod
    def _validated_result(cls, value: object) -> object:
        """结果按判别联合校验; 旧行与坏数据读不出来时置空, 不做迁移."""
        if value is None or isinstance(value, BaseModel):
            return value
        try:
            return _RESULT_ADAPTER.validate_python(value)
        except ValueError:
            return None


class TaskListResponse(BaseModel):
    items: list[TaskListItem]
    total: int


class TaskChildListResponse(BaseModel):
    items: list[TaskChildItem]
    total: int
    """不受本页 limit/offset 截断."""


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
    media_id: int | None = Field(default=None, description="MediaFile ID. 通常仅用于内部提交任务，手动提交无需指定")
    content_type: ContentType | None = Field(
        default=None, description="内容类型; 未给出时: 仅 media_id 按文件路径推断, 有 number 时按番号推断"
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

    async def resolve(self, repo: Repository, *, rules: NumberRules = EMPTY_NUMBER_RULES) -> ScrapePayload:
        """media 不存在时抛 HTTPException(404)."""
        if self.media_id is not None:
            media = await repo.get_media_file(self.media_id)
            if media is None:
                raise HTTPException(status_code=404, detail=f"媒体文件 {self.media_id} 不存在")
            if self.number is not None:
                return ScrapePayload(
                    number=self.number,
                    content_type=self.content_type or infer_content_type(self.number, rules=rules),
                    media_file_id=self.media_id,
                    use_cache=self.use_cache,
                )
            parsed = parse_file_info(media.path, rules=rules)
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
            content_type=self.content_type or infer_content_type(self.number, rules=rules),
            use_cache=self.use_cache,
        )


class RefreshSubmission(RefreshPayload):
    type: Literal["refresh"]


class OrganizeSubmission(OrganizePayload):
    type: Literal["organize"]


class ScanInvalidSubmission(ScanInvalidPayload):
    type: Literal["scan_invalid"]


class DeleteSubmission(DeletePayload):
    type: Literal["delete"]


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
    | ScanInvalidSubmission
    | DeleteSubmission
    | ScrapeSubmission
    | CleanupSubmission
    | UpscaleSubmission
    | R18ImportSubmission
    | ActorScrapeSubmission
    | RescrapeSubmission,
    Field(discriminator="type"),
]


RoutineSubmission = Annotated[
    CleanupSubmission | UpscaleSubmission | R18ImportSubmission | RescrapeSubmission,
    Field(discriminator="type"),
]
