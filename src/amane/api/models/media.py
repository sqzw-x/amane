from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ...db import MediaFile, MediaFileStatus
from ...parsing import ContentType, Mosaic
from ...utils.model import create_partial_model


class MediaFileResponse(BaseModel):
    id: int
    path: str
    oshash: str | None = None
    size: int | None = None
    duration: float | None = None
    codec: str | None = None
    number: str | None = None
    status: MediaFileStatus
    content_type: ContentType
    mosaic: Mosaic | None = None
    has_subtitle: bool = False
    definition: str | None = None
    metadata_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaListResponse(BaseModel):
    items: list[MediaFileResponse]
    total: int


if TYPE_CHECKING:
    type MediaFileUpdateRequest = MediaFile

MediaFileUpdateRequest = create_partial_model(
    MediaFile, fields=("status", "number", "path", "metadata_id"), partial_cls_name="MediaFileUpdateRequest"
)
