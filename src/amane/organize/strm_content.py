"""link_mode=strm 时 .strm 正文的库级模板.

与路径模板共用占位符解析与填充, 不折叠路径段、不检查 safe_dirs.
空模板保持默认: 一行视频绝对路径.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from .path_templates import (
    dest_template_variables,
    render_content_template,
    template_uses_placeholder,
    validate_path_template,
    video_relpath,
)

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
    """单行; 空白合法; 占位符语法与路径模板相同."""
    if "\n" in value or "\r" in value:
        raise ValueError("strm_content_template must be a single line")
    return validate_path_template(value)


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
) -> str:
    """渲染 .strm 正文 (含末尾换行). 空模板写 dest 的字面绝对路径.

    模板引用 `{video_relpath}` 且 dest 不在库根下时抛 ValueError, 不写出错误正文.
    """
    normalized = normalize_strm_content_template(template)
    if normalized is None:
        return f"{dest}\n"
    if template_uses_placeholder(normalized, "video_relpath"):
        video_relpath(dest, library_root)
    variables = dest_template_variables(
        metadata, dest, library_root, source_path=source_path, file_info=file_info, link=link
    )
    rendered = render_content_template(normalized, variables)
    return rendered if rendered.endswith("\n") else f"{rendered}\n"
