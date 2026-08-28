from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from sqlalchemy import UnaryExpression, asc, desc, exists, func
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from amane.enums import ActorGender, DownloadableResource, LibraryAutomation, LinkMode, MoveMode
from amane.utils.dates import normalize_calendar_date

from .models import (
    ActorSortField,
    FacetSortField,
    MediaFile,
    MediaFileStatus,
    MediaSortField,
    Metadata,
    MetadataSortField,
    RoutineType,
    SortOrder,
    Task,
    TaskSortField,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ==================== 排序列映射 ====================
#
# 显式枚举 -> Column 映射. 不用 getattr(Model, field.value) 反射, 以便:
#   1. 类型检查器能验证每个 Column 真实存在;
#   2. 排序字段集合与可排序列严格对应, 新增枚举值忘记映射即 KeyError 而非静默.

# 列排序字段 → Column. FILE_COUNT 不在此映射, 见 _metadata_primary_order.
_METADATA_SORT_COLUMNS: dict[MetadataSortField, Mapped[Any]] = {
    MetadataSortField.NUMBER: col(Metadata.number),
    MetadataSortField.TITLE: col(Metadata.title),
    MetadataSortField.STUDIO: col(Metadata.studio),
    MetadataSortField.RELEASE: col(Metadata.release),
    MetadataSortField.CREATED_AT: col(Metadata.created_at),
    MetadataSortField.UPDATED_AT: col(Metadata.updated_at),
}

_MEDIA_SORT_COLUMNS: dict[MediaSortField, Mapped[Any]] = {
    MediaSortField.NUMBER: col(MediaFile.number),
    MediaSortField.PATH: col(MediaFile.path),
    MediaSortField.STATUS: col(MediaFile.status),
    MediaSortField.SIZE: col(MediaFile.size),
    MediaSortField.CREATED_AT: col(MediaFile.created_at),
    MediaSortField.UPDATED_AT: col(MediaFile.updated_at),
}

_TASK_SORT_COLUMNS: dict[TaskSortField, Mapped[Any]] = {
    TaskSortField.TYPE: col(Task.type),
    TaskSortField.STATUS: col(Task.status),
    TaskSortField.PRIORITY: col(Task.priority),
    TaskSortField.CREATED_AT: col(Task.created_at),
    TaskSortField.STARTED_AT: col(Task.started_at),
    TaskSortField.FINISHED_AT: col(Task.finished_at),
}


def _order_clause[T](column: Mapped[T], order: SortOrder) -> UnaryExpression[T]:
    return asc(column) if order == SortOrder.ASC else desc(column)


def _metadata_file_count_expr() -> ColumnElement[int]:
    """关联 MediaFile 数量的相关子查询 (排序用)."""
    return (
        select(func.count())
        .select_from(MediaFile)
        .where(col(MediaFile.metadata_id) == col(Metadata.id))
        .correlate(Metadata)
        .scalar_subquery()
    )


def _metadata_primary_order(sort_by: MetadataSortField, order: SortOrder) -> ColumnElement[Any]:
    """Metadata 列表主排序键; FILE_COUNT 走计数表达式, 其余走列映射."""
    if sort_by is MetadataSortField.FILE_COUNT:
        expr = _metadata_file_count_expr()
        return asc(expr) if order == SortOrder.ASC else desc(expr)
    return _order_clause(_METADATA_SORT_COLUMNS[sort_by], order)


def _metadata_has_files_clause(*, has_files: bool) -> ColumnElement[bool]:
    file_exists = exists().where(col(MediaFile.metadata_id) == col(Metadata.id))
    return file_exists if has_files else ~file_exists


def _facet_primary_order(sort_by: FacetSortField, order: SortOrder, *, name_col: Any, count_expr: Any) -> Any:
    primary = name_col if sort_by == FacetSortField.NAME else count_expr
    return asc(primary) if order == SortOrder.ASC else desc(primary)


class FacetItem(BaseModel):
    """分类目录条目 + 关联影片数."""

    id: int
    name: str
    count: int


class ActorBrowseItem(BaseModel):
    """演员浏览列表行 - 卡片/表格字段 + 关联影片数 (不含简介/别名/源字典)."""

    id: int
    name: str
    count: int
    aliases: list[str] = []
    gender: ActorGender = ActorGender.UNKNOWN
    birthday: str | None = None
    birthplace: str | None = None
    height: int | None = None
    bust: int | None = None
    waist: int | None = None
    hip: int | None = None
    cup: str | None = None
    overview: str | None = None
    tagline: str | None = None
    image_urls: list[str] = []
    provider_ids: dict[str, str] = {}
    source_urls: dict[str, str] = {}
    field_sources: dict[str, str] = {}
    updated_at: datetime | None = None


class ActorBrowseParams(BaseModel):
    """演员列表查询 - GET /actors 与 ``browse_actors`` 共用 (含校验与规范化)."""

    search: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)
    sort_by: ActorSortField = ActorSortField.NAME
    order: SortOrder = SortOrder.ASC
    has_person: bool | None = Field(default=None, description="是否已有人物标量/简介")
    has_image: bool | None = Field(default=None, description="是否已有头像 URL")
    gender: list[ActorGender] | None = Field(default=None, description="性别多选 (可重复)")
    birthday_min: str | None = Field(default=None, description="生日下界 YYYY-MM-DD")
    birthday_max: str | None = Field(default=None, description="生日上界 YYYY-MM-DD")
    height_min: int | None = Field(default=None, ge=0, description="身高下界 cm")
    height_max: int | None = Field(default=None, ge=0, description="身高上界 cm")
    bust_min: int | None = Field(default=None, ge=0, description="胸围下界 cm")
    bust_max: int | None = Field(default=None, ge=0, description="胸围上界 cm")
    waist_min: int | None = Field(default=None, ge=0, description="腰围下界 cm")
    waist_max: int | None = Field(default=None, ge=0, description="腰围上界 cm")
    hip_min: int | None = Field(default=None, ge=0, description="臀围下界 cm")
    hip_max: int | None = Field(default=None, ge=0, description="臀围上界 cm")
    cup_min: str | None = Field(default=None, description="罩杯下界 (字母序, 大小写不敏感)")
    cup_max: str | None = Field(default=None, description="罩杯上界")
    birthplace: str | None = Field(default=None, description="出生地包含匹配")
    ids: list[int] | None = Field(default=None, description="限制为这些演员主键")
    saved_query_id: int | None = Field(
        default=None, description="Saved query preset id; AND with other filters via SQL subquery"
    )

    @field_validator("search", "birthplace", mode="before")
    @classmethod
    def _strip_blank(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return value

    @field_validator("birthday_min", "birthday_max", mode="after")
    @classmethod
    def _normalize_birthday(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        normalized = normalize_calendar_date(value)
        if normalized is None:
            raise ValueError(f"{info.field_name} must be YYYY-MM-DD")
        return normalized

    @field_validator("cup_min", "cup_max", mode="after")
    @classmethod
    def _normalize_cup(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().upper()
        return text or None

    @field_validator("gender", mode="after")
    @classmethod
    def _dedupe_gender(cls, value: list[ActorGender] | None) -> list[ActorGender] | None:
        if not value:
            return None
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _reject_inverted_ranges(self) -> ActorBrowseParams:
        def inverted_str(lo: str | None, hi: str | None) -> bool:
            return lo is not None and hi is not None and lo > hi

        def inverted_int(lo: int | None, hi: int | None) -> bool:
            return lo is not None and hi is not None and lo > hi

        checks: tuple[tuple[str, bool], ...] = (
            ("birthday", inverted_str(self.birthday_min, self.birthday_max)),
            ("height", inverted_int(self.height_min, self.height_max)),
            ("bust", inverted_int(self.bust_min, self.bust_max)),
            ("waist", inverted_int(self.waist_min, self.waist_max)),
            ("hip", inverted_int(self.hip_min, self.hip_max)),
            ("cup", inverted_str(self.cup_min, self.cup_max)),
        )
        for name, bad in checks:
            if bad:
                raise ValueError(f"{name}_min must be <= {name}_max")
        return self


# ==================== TypedDicts for update operations ====================


class MetadataFields(TypedDict, total=False):
    """upsert_metadata / update_metadata 可接受的字段."""

    title: str | None
    actors: list[str]
    studio: str | None
    publisher: str | None
    release: str | None
    runtime: int | None
    tags: list[str]
    series: str | None
    plot: str | None
    directors: list[str]
    poster_urls: list[str]
    thumb_urls: list[str]
    trailer_urls: list[str]
    extrafanart_urls: dict[str, list[str]]
    scores: dict[str, float]
    external_ids: dict[str, str]
    source_urls: dict[str, str]
    field_sources: dict[str, str]
    raw: dict[str, dict[str, object]]


class MediaFileUpdates(TypedDict, total=False):
    """update_media_file 可接受的字段."""

    path: str
    number: str | None
    oshash: str | None
    size: int | None
    duration: float | None
    codec: str | None
    status: MediaFileStatus
    metadata_id: int | None


class LibraryUpdates(TypedDict, total=False):
    """update_library 可接受的字段."""

    name: str
    path: str
    automation: LibraryAutomation
    recursive: bool
    patterns: list[str]
    move_mode: MoveMode
    video_template: str
    link_template: str | None
    link_mode: LinkMode
    cd_suffix_template: str
    thumb_template: str | None
    poster_template: str | None
    fanart_template: str | None
    extrafanart_template: str | None
    nfo_template: str | None
    trailer_template: str | None
    subtitle_template: str | None
    subtitle_extensions: list[str]
    write_nfo: bool
    copy_resources: list[DownloadableResource]
    trailer_pattern: str
    blacklist_patterns: list[str]
    min_file_size: int


class ScheduleUpdates(TypedDict, total=False):
    """update_schedule 可接受的字段."""

    name: str | None
    cron: str
    task_type: RoutineType
    payload: dict[str, object]
    enabled: bool
    last_run: datetime | None
    next_run: datetime | None


class FeedUpdates(TypedDict, total=False):
    """update_feed 可接受的字段."""

    name: str
    url: str
    group: str
    enabled: bool
    auto_enqueue: bool
    interval_seconds: int
    number_pattern: str | None
    content_type: str | None
    use_cache: list[str]
    etag: str | None
    last_modified: str | None
    next_fetch_at: datetime | None
    last_fetched_at: datetime | None
    last_error: str | None
    last_enqueued: int


class ActorPersonFields(TypedDict, total=False):
    """update_actor / 刮削回写可接受的人物字段 (不含 name/id)."""

    aliases: list[str]
    gender: ActorGender
    birthday: str | None
    birthplace: str | None
    height: int | None
    bust: int | None
    waist: int | None
    hip: int | None
    cup: str | None
    overview: str | None
    tagline: str | None
    image_urls: list[str]
    provider_ids: dict[str, str]
    source_urls: dict[str, str]
    field_sources: dict[str, str]
    raw: dict[str, dict[str, object]]


class CommentUpdates(TypedDict, total=False):
    """update_comment 可接受的字段."""

    body: str


class UserTagUpdates(TypedDict, total=False):
    """update_user_tag 可接受的字段."""

    name: str


# ==================== Repository ====================
