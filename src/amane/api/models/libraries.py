from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ...db import Library
from ...enums import DownloadableResource, LibraryAutomation, MoveMode
from ...organize.path_templates import CD_SUFFIX_TEMPLATE_DEFAULT, CdSuffixTemplate
from ...utils.extensions import DEFAULT_TRAILER_PATTERN, TrailerPattern
from ...utils.model import create_partial_model


class LibraryCreateRequest(BaseModel):
    name: str | None = None
    """显示名; 留空则取路径 basename."""
    path: str
    automation: LibraryAutomation = LibraryAutomation.SCRAPE
    recursive: bool = True
    patterns: list[str] = []
    move_mode: MoveMode = MoveMode.MOVE
    video_template: str = "{studio}/{number}/{number}.{ext}"
    cd_suffix_template: CdSuffixTemplate = CD_SUFFIX_TEMPLATE_DEFAULT
    thumb_template: str | None = None
    poster_template: str | None = None
    fanart_template: str | None = None
    extrafanart_template: str | None = None
    nfo_template: str | None = None
    trailer_template: str | None = None
    subtitle_template: str | None = None
    write_nfo: bool = True
    copy_resources: list[DownloadableResource] = Field(default_factory=lambda: list(DownloadableResource))
    trailer_pattern: TrailerPattern = DEFAULT_TRAILER_PATTERN
    scan: bool = True


if TYPE_CHECKING:
    type LibraryUpdateRequest = Library

# 外部可写面: 除主键 id 外的全部库配置列.
LibraryUpdateRequest = create_partial_model(Library, ignore_fields=("id",), partial_cls_name="LibraryUpdateRequest")


class LibraryResponse(BaseModel):
    id: int
    name: str
    path: str
    automation: LibraryAutomation
    recursive: bool
    patterns: list[str] = []
    move_mode: MoveMode
    video_template: str
    cd_suffix_template: str
    thumb_template: str | None = None
    poster_template: str | None = None
    fanart_template: str | None = None
    extrafanart_template: str | None = None
    nfo_template: str | None = None
    trailer_template: str | None = None
    subtitle_template: str | None = None
    write_nfo: bool
    copy_resources: list[DownloadableResource]
    trailer_pattern: str


class LibraryListResponse(BaseModel):
    items: list[LibraryResponse]


class PathTemplatePlaceholder(BaseModel):
    name: str
    phase: Literal["metadata", "source", "file", "post_video"]


class PathTemplateSchemaResponse(BaseModel):
    """路径模板 UI 契约: 占位符相位 + 默认值, 与 resolve_paths 同源."""

    video_default: str
    cd_suffix_default: str
    optional_defaults: dict[str, str]
    placeholders: list[PathTemplatePlaceholder]
