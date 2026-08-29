from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ...db import Library
from ...enums import DownloadableResource, LibraryAutomation, LinkMode, MoveMode
from ...organize.path_templates import (
    VIDEO_TEMPLATE_DEFAULT,
    OptionalPathTemplate,
    PathTemplate,
    PlaceholderPhase,
)
from ...utils.extensions import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
)
from ...utils.model import create_partial_model


class LibraryCreateRequest(BaseModel):
    name: str | None = None
    """显示名; 留空则取路径 basename."""
    path: str
    automation: LibraryAutomation = LibraryAutomation.SCRAPE
    recursive: bool = True
    patterns: list[str] = []
    move_mode: MoveMode = MoveMode.MOVE
    video_template: PathTemplate = VIDEO_TEMPLATE_DEFAULT
    link_template: OptionalPathTemplate = None
    link_mode: LinkMode = LinkMode.STRM
    thumb_template: OptionalPathTemplate = None
    poster_template: OptionalPathTemplate = None
    fanart_template: OptionalPathTemplate = None
    extrafanart_template: OptionalPathTemplate = None
    nfo_template: OptionalPathTemplate = None
    trailer_template: OptionalPathTemplate = None
    subtitle_template: OptionalPathTemplate = None
    subtitle_extensions: SubtitleExtensions = Field(default_factory=lambda: list(DEFAULT_SUBTITLE_EXTENSIONS))
    write_nfo: bool = True
    copy_resources: list[DownloadableResource] = Field(default_factory=lambda: list(DownloadableResource))
    trailer_pattern: TrailerPattern = DEFAULT_TRAILER_PATTERN
    blacklist_patterns: list[BlacklistPattern] = []
    """文件名正则列表; 命中任一则扫描/监控跳过, ORGANIZE 时移入库根 `.amane_trash`."""
    min_file_size: MinFileSize = 0
    """视频体积下限 (字节). 小于此值的扫描视频跳过入库, ORGANIZE 时进 `.amane_trash`. 0 关闭."""
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
    link_template: str | None = None
    link_mode: LinkMode
    thumb_template: str | None = None
    poster_template: str | None = None
    fanart_template: str | None = None
    extrafanart_template: str | None = None
    nfo_template: str | None = None
    trailer_template: str | None = None
    subtitle_template: str | None = None
    subtitle_extensions: list[str]
    write_nfo: bool
    copy_resources: list[DownloadableResource]
    trailer_pattern: str
    blacklist_patterns: list[str]
    min_file_size: int


class LibraryListResponse(BaseModel):
    items: list[LibraryResponse]


class PathTemplatePlaceholder(BaseModel):
    name: str
    phase: PlaceholderPhase


class PathTemplateSchemaResponse(BaseModel):
    """路径模板 UI 契约: 占位符相位 + 默认值, 与 resolve_paths 同源."""

    video_default: str
    optional_defaults: dict[str, str]
    placeholders: list[PathTemplatePlaceholder]
    subtitle_extensions_default: list[str]
