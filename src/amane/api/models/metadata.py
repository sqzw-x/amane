from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

from ...db import Metadata
from ...enums import ActorGender, MetadataField
from ...handlers import CacheKind
from ...parsing import ContentType, Mosaic
from ...utils.model import anyof_extras, create_partial_model, kv
from .comments import CommentResponse
from .crop import CropBoxRequest
from .media import MediaFileResponse
from .user_tags import UserTagResponse

_METADATA_FIELD_VALUES = frozenset(str(field) for field in MetadataField)


class FilePhaseSummary(BaseModel):
    """关联文件相位聚合: 任一文件具备即亮; definition 取最高档."""

    has_subtitle: bool = False
    uncensored: bool = False
    mosaics: list[Mosaic] = []
    definition: str | None = None


class MetadataResponse(BaseModel):
    id: int
    number: str
    title: str | None = None
    actors: list[str] = []
    studio: str | None = None
    publisher: str | None = None
    release: str | None = None
    runtime: int | None = None
    tags: list[str] = []
    series: str | None = None
    plot: str | None = None
    directors: list[str] = []
    poster_url: str | None = None
    thumb_url: str | None = None
    trailer_url: str | None = None
    extrafanart: list[str] = []
    score: float | None = None
    vr: bool = False
    poster_urls: list[str] = []
    thumb_urls: list[str] = []
    trailer_urls: list[str] = []
    extrafanart_urls: dict = {}
    scores: dict[str, float] = {}
    external_ids: dict = {}
    source_urls: dict = {}
    field_sources: dict = {}
    raw: dict = {}
    locked_fields: list[MetadataField] = []

    @field_validator("locked_fields", mode="before")
    @classmethod
    def _drop_unknown_locks(cls, value: object) -> object:
        """非法存量锁值忽略."""
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in _METADATA_FIELD_VALUES))

    file_count: int = 0
    file_phase: FilePhaseSummary = Field(default_factory=FilePhaseSummary)
    created_at: datetime | None = None
    updated_at: datetime | None = None


if TYPE_CHECKING:
    type PartialMetadata = Metadata

# 外部可写字段: 排除只读列 (id/number/时间戳)、仅后端可写字段 (raw/field_sources 由刮削写入)、
# 投影列 (vr/score_rank 由写方法从真值重算) 与锁列 (经 PUT locks 管理).
PartialMetadata = create_partial_model(
    Metadata,
    ignore_fields=(
        "id",
        "number",
        "created_at",
        "updated_at",
        "raw",
        "field_sources",
        "locked_fields",
        "vr",
        "score_rank",
    ),
    json_schema_extras={
        "extrafanart_urls": anyof_extras(kv({"v-x-long": True})),
        "release": anyof_extras(
            {
                "description": "发行日 (YYYY-MM-DD); 也可输入带时刻的 ISO 串, 服务端只保留日期",
                "examples": ["2020-01-01"],
            }
        ),
    },
)


class MetadataListResponse(BaseModel):
    items: list[MetadataResponse]
    total: int


class MetadataDetailResponse(BaseModel):
    metadata: MetadataResponse
    files: list[MediaFileResponse]
    user_tags: list[UserTagResponse] = []
    comments: list[CommentResponse] = []
    actor_ids: dict[str, int] = {}
    actor_genders: dict[str, ActorGender] = {}
    director_ids: dict[str, int] = {}
    tag_ids: dict[str, int] = {}
    studio_id: int | None = None
    publisher_id: int | None = None
    series_id: int | None = None


class MergeRequest(BaseModel):
    selections: dict[str, str] = Field(description="field_name -> source_key 映射")


class MetadataLocksRequest(BaseModel):
    """整体替换锁定字段集合."""

    fields: list[MetadataField] = Field(default_factory=list, description="锁定的字段集合; 空集解除全部锁定")


class CropPosterRequest(CropBoxRequest):
    """从封面图按像素框裁切海报 (相对 thumb 当前本地文件像素; 含就地超分后尺寸)."""


class MetadataBatchIdsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="Metadata ID 列表")


class MetadataBatchDeleteResponse(BaseModel):
    deleted: int = Field(description="成功删除的数量")
    missing: int = Field(description="不存在的 id 数量")


class MetadataBatchScrapeRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="Metadata ID 列表")
    content_type: ContentType | None = Field(
        default=None, description="内容类型; None = 服务端推断 (挂载文件路径 → 番号模式)"
    )
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 DB per-site 快照; trans: 复用译文). 空集 = 全部强制刷新",
    )


class MetadataBatchScrapeResponse(BaseModel):
    submitted: int = Field(description="成功提交的任务数")
    missing: int = Field(description="不存在的 id 数量")
    task_ids: list[int] = Field(description="提交的任务 id 列表")


class MetadataUserTagsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="Metadata ID 列表")
    user_tag_ids: list[int] = Field(min_length=1, description="用户标签 ID 列表")
    action: Literal["attach", "detach"] = Field(description="attach 为并入, detach 为移除; 两者均幂等")
