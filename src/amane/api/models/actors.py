from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from ...db.models import Actor
from ...enums import ActorField, ActorGender
from ...handlers.models import CacheKind
from ...utils.model import create_partial_model
from .crop import CropBoxRequest
from .user_tags import UserTagResponse

_ACTOR_FIELD_VALUES = frozenset(str(field) for field in ActorField)


def normalize_actor_locks(value: object) -> list[ActorField]:
    """过滤非法存量锁值并去重 (保序)."""
    if not isinstance(value, list):
        return []
    out: list[ActorField] = []
    for item in value:
        if not isinstance(item, str) or item not in _ACTOR_FIELD_VALUES:
            continue
        field = ActorField(item)
        if field not in out:
            out.append(field)
    return out


class ActorResponse(BaseModel):
    """详情填全量; 列表 (`GET /actors`) 只填卡片/表格字段, 其余 (简介/别名/标签/源字典/raw/锁) 为空."""

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
    locked_fields: list[ActorField] = []
    updated_at: datetime | None = None

    @field_validator("locked_fields", mode="before")
    @classmethod
    def _drop_unknown_locks(cls, value: object) -> object:
        """非法存量锁值忽略."""
        return normalize_actor_locks(value)


class ActorListResponse(BaseModel):
    items: list[ActorResponse]
    total: int


class ActorScrapeRequest(BaseModel):
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 Actor.raw; trans: 复用演员译文). 空集 = 全部强制刷新",
    )


class ActorLocksRequest(BaseModel):
    """整体替换锁定字段集合."""

    fields: list[ActorField] = Field(default_factory=list, description="锁定的字段集合; 空集解除全部锁定")


class ActorUserTagsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="演员 ID 列表")
    user_tag_ids: list[int] = Field(min_length=1, description="用户标签 ID 列表")
    action: Literal["attach", "detach"] = Field(description="attach 为并入, detach 为移除; 两者均幂等")


class CropAvatarRequest(CropBoxRequest):
    """从当前主图按像素框裁切头像 (相对 image_urls[0] 当前本地文件像素; 含就地超分后尺寸)."""


if TYPE_CHECKING:
    type ActorUpdateRequest = Actor

# 外部可写字段: 排除主键/展示名/时间戳, 仅刮削写入的 raw/field_sources 与锁列.
# aliases 不是 DB 列 (行化后经由 ActorAlias), 经 extra_fields 显式纳入可写字段.
ActorUpdateRequest = create_partial_model(
    Actor,
    ignore_fields=("id", "name", "created_at", "updated_at", "raw", "field_sources", "locked_fields"),
    partial_cls_name="ActorUpdateRequest",
    extra_fields={"aliases": Annotated[list[str], Field(description="别名行 (保序), 整表替换")]},
)

__all__ = [
    "ActorListResponse",
    "ActorLocksRequest",
    "ActorResponse",
    "ActorScrapeRequest",
    "ActorUpdateRequest",
]
