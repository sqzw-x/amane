from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field, model_validator

from ...db import Library, Task
from ...enums import DownloadableResource, LibraryAutomation, LibraryIngest, LinkMode, MoveMode
from ...library import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
    resolve_ingest_cloud_path,
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
    auto_organize: bool = False
    ingest: LibraryIngest = LibraryIngest.NATIVE
    cloud_path: str | None = None
    """CloudDrive 虚拟路径 (POSIX, 如 /115open/云下载). ingest=clouddrive 时必填."""
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
    """文件名正则列表; 命中任一则扫描/监控跳过, 并作为无效文件进入清理清单."""
    min_file_size: MinFileSize = 0
    """最小视频大小 (字节). 小于此值的扫描视频跳过入库, 并作为无效文件进入清理清单. 0 关闭."""
    scan: bool = True

    @model_validator(mode="after")
    def _cloud_path_for_ingest(self) -> Self:
        self.cloud_path = resolve_ingest_cloud_path(self.ingest, self.cloud_path)
        return self


if TYPE_CHECKING:
    type LibraryUpdateRequest = Library

# 外部可写字段: 除主键 id 外的全部库配置列.
LibraryUpdateRequest = create_partial_model(Library, ignore_fields=("id",), partial_cls_name="LibraryUpdateRequest")


class LibraryLastOrganizeStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"


class LibraryLastOrganize(BaseModel):
    """该库最近一次终态整理任务的结果 (真值在任务里, 这里只做库级读取).

    手动整理与自动整理不区分; 失败任务没有结果载荷时 `error` 承载原因, 计数为 0.
    """

    status: LibraryLastOrganizeStatus
    at: datetime | None = None
    organized: int = 0
    skipped: int = 0
    conflicted: int = 0
    failed: int = 0
    error: str | None = None


class LibraryResponse(BaseModel):
    id: int
    name: str
    path: str
    automation: LibraryAutomation
    auto_organize: bool
    last_organize: LibraryLastOrganize | None = None
    ingest: LibraryIngest
    cloud_path: str | None = None
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


def last_organize_from_task(task: Task | None) -> LibraryLastOrganize | None:
    """终态整理任务 → 库页面摘要; 失败任务没有结果载荷时保留 `error`.

    `result` 是 JSON 列, 键缺失按 0 (失败行与旧行都可能不完整). SQLite 读回的 datetime 无 tzinfo, 补 UTC.
    """
    if task is None:
        return None
    result = task.result if isinstance(task.result, dict) else {}
    return LibraryLastOrganize(
        status=LibraryLastOrganizeStatus(task.status.value),
        at=task.finished_at.replace(tzinfo=UTC)
        if task.finished_at and task.finished_at.tzinfo is None
        else task.finished_at,
        organized=_count(result, "organized"),
        skipped=_count(result, "skipped"),
        conflicted=_count(result, "conflicted"),
        failed=_count(result, "failed"),
        error=task.error,
    )


def _count(result: dict[str, object], key: str) -> int:
    value = result.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


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
    """与 resolve_paths 同源."""

    video_default: str
    optional_defaults: OptionalPathTemplateDefaults
    placeholders: list[PathTemplatePlaceholder]
    subtitle_extensions_default: list[str]


def path_template_schema() -> PathTemplateSchemaResponse:
    return PathTemplateSchemaResponse(
        video_default=VIDEO_TEMPLATE_DEFAULT,
        optional_defaults=OPTIONAL_TEMPLATE_DEFAULTS,
        placeholders=[
            PathTemplatePlaceholder(name=name, map_keys=list(PLACEHOLDER_MAP_KEYS.get(name, ())))
            for name in PLACEHOLDERS
        ],
        subtitle_extensions_default=list(DEFAULT_SUBTITLE_EXTENSIONS),
    )
