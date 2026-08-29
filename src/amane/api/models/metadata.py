from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from ...db import Metadata
from ...handlers import CacheKind
from ...parsing import ContentType, Mosaic
from ...utils.model import anyof_extras, create_partial_model, kv
from .comments import CommentResponse
from .media import MediaFileResponse
from .user_tags import UserTagResponse


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
    # 计算属性 (展示用单值)
    poster_url: str | None = None
    thumb_url: str | None = None
    trailer_url: str | None = None
    extrafanart: list[str] = []
    score: float | None = None
    # 原始可写字段 (供编辑表单回填)
    poster_urls: list[str] = []
    thumb_urls: list[str] = []
    trailer_urls: list[str] = []
    extrafanart_urls: dict = {}
    scores: dict[str, float] = {}
    external_ids: dict = {}
    source_urls: dict = {}
    # 只读展示
    field_sources: dict = {}
    raw: dict = {}
    file_count: int = 0
    file_phase: FilePhaseSummary = Field(default_factory=FilePhaseSummary)
    created_at: datetime | None = None
    updated_at: datetime | None = None


if TYPE_CHECKING:
    type PartialMetadata = Metadata

# 外部可写面: 排除只读列 (id/number/时间戳) 与仅后端可写字段 (raw/field_sources 由刮削写入, 前端只读展示).
PartialMetadata = create_partial_model(
    Metadata,
    ignore_fields=("id", "number", "created_at", "updated_at", "raw", "field_sources"),
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
    # 名称 → 分类实体 id, 供前端 Badge 跳转筛选
    actor_ids: dict[str, int] = {}
    director_ids: dict[str, int] = {}
    tag_ids: dict[str, int] = {}
    studio_id: int | None = None
    publisher_id: int | None = None
    series_id: int | None = None


class MergeRequest(BaseModel):
    """合并请求: 按字段指定选用哪个来源的 raw 数据."""

    selections: dict[str, str] = Field(description="field_name -> source_key 映射")


class CropPosterRequest(BaseModel):
    """从封面图按像素框裁切海报 (相对 thumb 当前本地文件像素; 含就地超分后尺寸)."""

    left: int = Field(ge=0, description="裁切框左边界 (含)")
    top: int = Field(ge=0, description="裁切框上边界 (含)")
    right: int = Field(gt=0, description="裁切框右边界 (不含)")
    bottom: int = Field(gt=0, description="裁切框下边界 (不含)")

    @model_validator(mode="after")
    def _box_positive_area(self) -> CropPosterRequest:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("裁切区域须为正矩形 (left < right, top < bottom)")
        return self


class MetadataBatchIdsRequest(BaseModel):
    """按 id 列表批量操作的通用请求体."""

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


class MetadataBatchUserTagsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="Metadata ID 列表")
    user_tag_id: int
    action: Literal["attach", "detach"]


class MetadataBatchUserTagsResponse(BaseModel):
    affected: int = Field(description="成功挂载/取消挂载的数量")
    missing: int = Field(description="不存在的 metadata id (或用户 tag 不存在时的全部 id) 数量")
