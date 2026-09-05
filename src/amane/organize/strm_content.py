"""STRM 正文不折叠空段, 以便保留 `https://`. 空模板写一行视频绝对路径."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from ..enums import ActorGender
from .template import Parser, StrmEngine, TemplateContext

if TYPE_CHECKING:
    from ..db.models import Metadata
    from ..parsing.file_info import FileInfo


def normalize_strm_content_template(value: str | None) -> str | None:
    """空白 strm_content_template 视为未设置 (写视频绝对路径)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_strm_content_template(value: str) -> str:
    """必须为单行; 空白合法; 占位符语法与路径模板相同."""
    if "\n" in value or "\r" in value:
        raise ValueError("strm_content_template must be a single line")
    Parser(value).parse()
    return value


StrmContentTemplate = Annotated[str, AfterValidator(validate_strm_content_template)]


def render_strm_content(
    template: str | None,
    dest: Path,
    library_root: Path,
    metadata: Metadata,
    *,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    link: Path | None = None,
    actor_genders: Mapping[str, ActorGender] | None = None,
) -> str:
    """空模板写 dest 的字面绝对路径.
    模板引用 `{video_relpath}` 且整理后的路径不在本库内时抛 ValueError, 不写出错误正文.
    """
    normalized = normalize_strm_content_template(template)
    if normalized is None:
        return f"{dest}\n"
    ctx = TemplateContext.from_metadata(
        metadata,
        ext=dest.suffix.lstrip("."),
        source_path=source_path,
        file_info=file_info,
        actor_genders=actor_genders,
    )
    ctx.apply_video(dest, library_root)
    ctx.apply_link(link)
    return StrmEngine(normalized).render(ctx)
