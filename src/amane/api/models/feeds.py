import re
from collections.abc import Collection
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from ...db import Feed
from ...db.feed_keywords import normalize_ignore_keywords
from ...handlers.models import CacheKind
from ...parsing import ContentType
from ...utils.model import create_partial_model

_DEFAULT_USE_CACHE = {CacheKind.metadata, CacheKind.trans}
_GROUP_MAX_LEN = 256
_INTERVAL_SECONDS_MIN = 60
_INTERVAL_SECONDS_MAX = 86400
_CACHE_KIND_ORDER = (CacheKind.metadata, CacheKind.trans)
"""缓存种类的固定顺序; 库内与响应都按此顺序出现."""


def use_cache_values(kinds: Collection[CacheKind]) -> list[str]:
    """把缓存种类集合按固定顺序摊成名称列表 (库内只存名称)."""
    return [kind.value for kind in _CACHE_KIND_ORDER if kind in kinds]


def _parse_use_cache(values: Collection[str]) -> list[str]:
    """非法名称报错而不是丢弃: PATCH 里静默忽略等于没改."""
    kinds: set[CacheKind] = set()
    for value in values:
        try:
            kinds.add(CacheKind(value))
        except ValueError as exc:
            raise ValueError(f"use_cache 只能是 {' / '.join(kind.value for kind in _CACHE_KIND_ORDER)}") from exc
    return use_cache_values(kinds)


def normalize_feed_group(value: str | None) -> str:
    """规范化斜杠伪路径: 去首尾/空白, 折叠重复分隔, 反斜杠当斜杠. 空串为未分组."""
    if value is None:
        return ""
    collapsed = value.strip().replace("\\", "/")
    parts: list[str] = []
    for raw in collapsed.split("/"):
        part = raw.strip()
        if not part:
            continue
        if part in (".", "..") or any(ord(ch) < 32 for ch in part):
            raise ValueError("group 路径段不能为 . / .. 或含控制字符")
        parts.append(part)
    result = "/".join(parts)
    if len(result) > _GROUP_MAX_LEN:
        raise ValueError(f"group 最长 {_GROUP_MAX_LEN} 字符")
    return result


def _validate_http_url(value: str) -> str:
    url = value.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url 必须以 http:// 或 https:// 开头")
    return url


def _validate_number_pattern(value: str | None) -> str | None:
    if value is None:
        return None
    pattern = value.strip()
    if not pattern:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"number_pattern 不是合法正则: {exc}") from exc
    return pattern


class FeedCreateRequest(BaseModel):
    name: str = ""
    url: str
    group: str = ""
    enabled: bool = True
    auto_enqueue: bool = True
    interval_seconds: int = Field(default=3600, ge=_INTERVAL_SECONDS_MIN, le=_INTERVAL_SECONDS_MAX)
    number_pattern: str | None = None
    content_type: ContentType | None = None
    use_cache: set[CacheKind] = Field(default_factory=lambda: set(_DEFAULT_USE_CACHE))
    ignore_keywords: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("number_pattern")
    @classmethod
    def _pattern(cls, value: str | None) -> str | None:
        return _validate_number_pattern(value)

    @field_validator("group")
    @classmethod
    def _group(cls, value: str) -> str:
        return normalize_feed_group(value)

    @field_validator("ignore_keywords")
    @classmethod
    def _ignore_keywords(cls, value: list[str]) -> list[str]:
        return normalize_ignore_keywords(value)


if TYPE_CHECKING:
    type _FeedUpdateRequest = Feed

# 可写字段从 Feed 派生, 与 FeedCreateRequest 覆盖同一批列.
_FeedUpdateRequest = create_partial_model(
    Feed,
    ignore_fields=(
        "id",
        "etag",
        "last_modified",
        "next_fetch_at",
        "last_fetched_at",
        "last_error",
        "last_enqueued",
    ),
    partial_cls_name="FeedUpdateRequestBase",
)


class FeedUpdateRequest(_FeedUpdateRequest):  # pyright: ignore[reportGeneralTypeIssues]  # 动态生成的 partial 基类
    # 校验必须落在请求模型: 路由里抛出的 ValueError 是 500, 而同一非法值经 POST 是 422.
    # 校验器都放行 null (显式 null 由派生模型的非空列检查拒绝), 保证只作用于显式提供的值.

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_http_url(value)

    @field_validator("group")
    @classmethod
    def _group(cls, value: str | None) -> str | None:
        return None if value is None else normalize_feed_group(value)

    @field_validator("interval_seconds")
    @classmethod
    def _interval_seconds(cls, value: int | None) -> int | None:
        if value is not None and not _INTERVAL_SECONDS_MIN <= value <= _INTERVAL_SECONDS_MAX:
            raise ValueError(f"interval_seconds 必须在 {_INTERVAL_SECONDS_MIN} 到 {_INTERVAL_SECONDS_MAX} 之间")
        return value

    @field_validator("number_pattern")
    @classmethod
    def _pattern(cls, value: str | None) -> str | None:
        return _validate_number_pattern(value)

    @field_validator("use_cache")
    @classmethod
    def _use_cache(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _parse_use_cache(value)

    @field_validator("ignore_keywords")
    @classmethod
    def _ignore_keywords(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_ignore_keywords(value)


class FeedResponse(BaseModel):
    id: int
    name: str
    url: str
    group: str = ""
    enabled: bool
    auto_enqueue: bool
    interval_seconds: int
    number_pattern: str | None = None
    content_type: ContentType | None = None
    use_cache: list[CacheKind] = Field(default_factory=list)
    ignore_keywords: list[str] = Field(default_factory=list)
    next_fetch_at: datetime | None = None
    last_fetched_at: datetime | None = None
    last_error: str | None = None
    last_enqueued: int = 0
    unread_count: int = 0


class FeedListResponse(BaseModel):
    items: list[FeedResponse]
    total: int


class FeedItemResponse(BaseModel):
    id: int
    feed_id: int
    item_key: str
    title: str | None = None
    link: str | None = None
    description: str | None = None
    number: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    ignored_at: datetime | None = None
    read_at: datetime | None = None
    metadata_id: int | None = None
    """当前库里同番号 Metadata 的 id; 无则空. 列表 JOIN, 不是 FeedItem 列."""


class FeedItemListResponse(BaseModel):
    items: list[FeedItemResponse]
    total: int


class FeedItemBatchAction(StrEnum):
    IGNORE = "ignore"
    UNIGNORE = "unignore"
    READ = "read"
    UNREAD = "unread"
    DELETE = "delete"
    SCRAPE = "scrape"


class FeedItemBatchRequest(BaseModel):
    action: FeedItemBatchAction
    ids: list[int] = Field(min_length=1, max_length=1000)


class FeedItemBatchResponse(BaseModel):
    affected: int
    missing: int
    skipped: int = 0
    submitted: int = 0
    task_ids: list[int] = Field(default_factory=list)
