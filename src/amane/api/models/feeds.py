import re
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from ...db import Feed
from ...handlers.models import CacheKind
from ...parsing import ContentType
from ...utils.model import create_partial_model

_DEFAULT_USE_CACHE = {CacheKind.metadata, CacheKind.trans}
_GROUP_MAX_LEN = 256


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
    interval_seconds: int = Field(default=3600, ge=60, le=86400)
    number_pattern: str | None = None
    content_type: ContentType | None = None
    use_cache: set[CacheKind] = Field(default_factory=lambda: set(_DEFAULT_USE_CACHE))

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


if TYPE_CHECKING:
    type FeedUpdateRequest = Feed

FeedUpdateRequest = create_partial_model(
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
    partial_cls_name="FeedUpdateRequest",
)


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
    next_fetch_at: datetime | None = None
    last_fetched_at: datetime | None = None
    last_error: str | None = None
    last_enqueued: int = 0


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
    ids: list[int] = Field(min_length=1)


class FeedItemBatchResponse(BaseModel):
    affected: int
    missing: int
    skipped: int = 0
    submitted: int = 0
    task_ids: list[int] = Field(default_factory=list)
