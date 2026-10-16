from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ...db import MediaFile, MediaFileStatus
from ...parsing import Mosaic
from ...utils.model import create_partial_model


class MediaFileResponse(BaseModel):
    id: int
    library_id: int
    path: str
    oshash: str | None = None
    size: int | None = None
    duration: float | None = None
    codec: str | None = None
    number: str | None = None
    status: MediaFileStatus
    mosaic: Mosaic | None = None
    has_subtitle: bool = False
    definition: str | None = None
    metadata_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaListResponse(BaseModel):
    items: list[MediaFileResponse]
    total: int


class MediaBatchDeleteRequest(BaseModel):
    """批量删除媒体文件记录; 不存在的 id 计入 missing."""

    ids: list[int] = Field(min_length=1, max_length=1000, description="MediaFile ID 列表; 上限防 SQLite 绑定变量超限")


class MediaBatchDeleteResponse(BaseModel):
    deleted: int = Field(description="成功删除的数量")
    missing: int = Field(description="不存在的 id 数量")


if TYPE_CHECKING:
    type MediaFileUpdateRequest = MediaFile

MediaFileUpdateRequest = create_partial_model(
    MediaFile, fields=("status", "number", "path", "metadata_id"), partial_cls_name="MediaFileUpdateRequest"
)
