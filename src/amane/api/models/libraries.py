from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ...db import Library
from ...enums import DownloadableResource, LibraryAutomation, LinkMode, MoveMode
from ...library import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
)
from ...organize.path_templates import (
    EXTRAFANART_TEMPLATE_DEFAULT,
    FANART_TEMPLATE_DEFAULT,
    NFO_TEMPLATE_DEFAULT,
    PLACEHOLDER_MAP_KEYS,
    PLACEHOLDERS,
    POSTER_TEMPLATE_DEFAULT,
    SUBTITLE_TEMPLATE_DEFAULT,
    THUMB_TEMPLATE_DEFAULT,
    TRAILER_TEMPLATE_DEFAULT,
    VIDEO_TEMPLATE_DEFAULT,
    PathTemplate,
)
from ...organize.strm_content import StrmContentTemplate
from ...utils.model import create_partial_model, subset_of


class LibraryCreateRequest(BaseModel):
    name: str | None = None
    """显示名; 留空则取路径 basename."""
    path: str
    automation: LibraryAutomation = LibraryAutomation.SCRAPE
    recursive: bool = True
    patterns: list[str] = []
    move_mode: MoveMode = MoveMode.MOVE
    video_template: PathTemplate = VIDEO_TEMPLATE_DEFAULT
    link_template: PathTemplate | None = None
    link_mode: LinkMode = LinkMode.STRM
    strm_content_template: StrmContentTemplate | None = None
    thumb_template: PathTemplate | None = None
    poster_template: PathTemplate | None = None
    fanart_template: PathTemplate | None = None
    extrafanart_template: PathTemplate | None = None
    nfo_template: PathTemplate | None = None
    trailer_template: PathTemplate | None = None
    subtitle_template: PathTemplate | None = None
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
    strm_content_template: str | None = None
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


@subset_of(Library, covariant=True)
class OptionalPathTemplateDefaults(BaseModel):
    """附属模板缺省 (Library 对应列为 None 时 ORGANIZE 使用)."""

    thumb_template: PathTemplate
    poster_template: PathTemplate
    fanart_template: PathTemplate
    extrafanart_template: PathTemplate
    nfo_template: PathTemplate
    trailer_template: PathTemplate
    subtitle_template: PathTemplate


OPTIONAL_TEMPLATE_DEFAULTS = OptionalPathTemplateDefaults(
    thumb_template=THUMB_TEMPLATE_DEFAULT,
    poster_template=POSTER_TEMPLATE_DEFAULT,
    fanart_template=FANART_TEMPLATE_DEFAULT,
    extrafanart_template=EXTRAFANART_TEMPLATE_DEFAULT,
    nfo_template=NFO_TEMPLATE_DEFAULT,
    trailer_template=TRAILER_TEMPLATE_DEFAULT,
    subtitle_template=SUBTITLE_TEMPLATE_DEFAULT,
)


class PathTemplatePlaceholder(BaseModel):
    name: str
    map_keys: list[str] = Field(
        default_factory=list,
        description="有闭合取值时列出规范 key, 供 `{name|k=v}` 映射校验与 UI 提示. 空则不校验映射 key.",
    )


class PathTemplateSchemaResponse(BaseModel):
    """GET /libraries/path-template-schema: 占位符、默认值与可映射 key, 与 resolve_paths 同源."""

    video_default: str
    optional_defaults: OptionalPathTemplateDefaults
    placeholders: list[PathTemplatePlaceholder]
    subtitle_extensions_default: list[str]


def path_template_schema() -> PathTemplateSchemaResponse:
    """组装路径模板契约, 供 GET /libraries/path-template-schema."""
    return PathTemplateSchemaResponse(
        video_default=VIDEO_TEMPLATE_DEFAULT,
        optional_defaults=OPTIONAL_TEMPLATE_DEFAULTS,
        placeholders=[
            PathTemplatePlaceholder(
                name=name,
                map_keys=list(PLACEHOLDER_MAP_KEYS.get(name, ())),
            )
            for name in PLACEHOLDERS
        ],
        subtitle_extensions_default=list(DEFAULT_SUBTITLE_EXTENSIONS),
    )
