from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field

from ...db.models import Actor
from ...enums import ActorGender
from ...handlers.models import CacheKind
from ...utils.model import create_partial_model
from .user_tags import UserTagResponse


class ActorResponse(BaseModel):
    """详情填全量; 列表 (`GET /actors`) 只填卡片/表格字段, 简介/别名/标签/源字典/raw 为空."""

    id: int
    name: str
    count: int = 0
    aliases: list[str] = Field(default_factory=list, description="别名行 (保序; 不含展示名)")
    user_tags: list[UserTagResponse] = Field(default_factory=list, description="用户标签 (仅详情)")
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
    image_urls: list[str] = Field(default_factory=list)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    source_urls: dict[str, str] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, dict[str, Any]] = Field(default_factory=dict)
    updated_at: datetime | None = None


class ActorListResponse(BaseModel):
    items: list[ActorResponse]
    total: int


class ActorScrapeRequest(BaseModel):
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 Actor.raw; trans: 预留译文). 空集 = 全部强制刷新",
    )


class ActorUserTagsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="演员 ID 列表")
    user_tag_ids: list[int] = Field(min_length=1, description="用户标签 ID 列表")
    action: Literal["attach", "detach"] = Field(description="attach 为并入, detach 为移除; 两者均幂等")


if TYPE_CHECKING:
    type ActorUpdateRequest = Actor

# 外部可写字段: 排除主键/展示名/时间戳与仅刮削写入的 raw/field_sources.
# aliases 不是 DB 列 (行化后经由 ActorAlias), 经 extra_fields 显式纳入可写字段.
ActorUpdateRequest = create_partial_model(
    Actor,
    ignore_fields=("id", "name", "created_at", "updated_at", "raw", "field_sources"),
    partial_cls_name="ActorUpdateRequest",
    extra_fields={"aliases": Annotated[list[str], Field(description="别名行 (保序), 整表替换")]},
)

__all__ = [
    "ActorListResponse",
    "ActorResponse",
    "ActorScrapeRequest",
    "ActorUpdateRequest",
]
