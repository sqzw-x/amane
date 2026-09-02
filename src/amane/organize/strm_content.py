"""link_mode=strm 时 .strm 正文的库级模板.

不是路径模板: 不执行 `_safe` / 空段折叠 / safe_dirs, 不写进 PLACEHOLDERS.
只替换 `{video_relpath}` (视频相对库根的 POSIX 路径, 无前导 /).
空模板保持默认: 一行视频绝对路径.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator

_PLACEHOLDER = "video_relpath"
_TOKEN = "{" + _PLACEHOLDER + "}"
_BRACE = re.compile(r"\{([^{}]*)\}")


def _lexical_abs(path: Path) -> Path:
    """转为绝对路径并消除 ``.`` / ``..``, 但不跟随符号链接."""
    return Path(os.path.abspath(os.fspath(path)))  # noqa: PTH100


def normalize_strm_content_template(value: str | None) -> str | None:
    """空白 strm_content_template 视为未设置 (写视频绝对路径)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_strm_content_template(value: str) -> str:
    """单行; 空白合法; 花括号内只允许 video_relpath."""
    if "\n" in value or "\r" in value:
        raise ValueError("strm_content_template must be a single line")
    stripped = value.strip()
    if not stripped:
        return stripped
    for match in _BRACE.finditer(stripped):
        name = match.group(1)
        if name != _PLACEHOLDER:
            raise ValueError(f"unknown strm content placeholder: {{{name}}}")
    if stripped.count("{") != stripped.count("}"):
        raise ValueError("unclosed placeholder in strm_content_template")
    return stripped


StrmContentTemplate = Annotated[str, AfterValidator(validate_strm_content_template)]


def video_relpath(dest: Path, library_root: Path) -> str:
    """dest 相对 library_root 的 POSIX 路径, 无前导 /. dest 必须落在库根下."""
    dest_abs = _lexical_abs(dest)
    root_abs = _lexical_abs(library_root)
    try:
        rel = dest_abs.relative_to(root_abs)
    except ValueError as exc:
        raise ValueError(f"video dest '{dest_abs}' is outside library root '{root_abs}'") from exc
    return rel.as_posix()


def render_strm_content(template: str | None, dest: Path, library_root: Path) -> str:
    """渲染 .strm 正文 (含末尾换行). 空模板写 dest 的字面绝对路径.

    模板引用 `{video_relpath}` 且 dest 不在库根下时抛 ValueError, 不写出错误正文.
    """
    normalized = normalize_strm_content_template(template)
    if normalized is None:
        return f"{dest}\n"
    rendered = normalized.replace(_TOKEN, video_relpath(dest, library_root)) if _TOKEN in normalized else normalized
    return rendered if rendered.endswith("\n") else f"{rendered}\n"
