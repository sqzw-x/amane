from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from ..enums import LinkMode
from ..parsing.file_info import DEFINITION_VALUES, MOSAIC_VALUES, FileInfo
from ..utils.path import is_any_descendant, is_descendant

if TYPE_CHECKING:
    from ..db.models import Library, Metadata

_UNKNOWN = "Unknown"
_DRIVE = re.compile(r"^[A-Za-z]:")
_DRIVE_ONLY = re.compile(r"^[A-Za-z]:$")


@dataclass
class ResolvedPaths:
    """渲染后的所有输出路径 (完整路径含扩展名)."""

    video: Path
    thumb: Path
    poster: Path
    fanart: Path
    extrafanart_dir: Path
    nfo: Path
    trailer: Path
    link: Path | None = None


# --- 默认模板 (当对应字段为 None 时使用) ---

VIDEO_TEMPLATE_DEFAULT = "{studio}/{number}/{number}[-CD{cd?}][-{sub?}].{ext}"
THUMB_TEMPLATE_DEFAULT = "{link_dir}/thumb.jpg"
POSTER_TEMPLATE_DEFAULT = "{link_dir}/poster.jpg"
FANART_TEMPLATE_DEFAULT = "{link_dir}/fanart.jpg"
EXTRAFANART_TEMPLATE_DEFAULT = "{link_dir}/extrafanart"
NFO_TEMPLATE_DEFAULT = "{link_dir}/{number}.nfo"
TRAILER_TEMPLATE_DEFAULT = "{link_dir}/trailer.mp4"
SUBTITLE_TEMPLATE_DEFAULT = "{link_dir}/{raw_srt_name}.{ext}"


PLACEHOLDERS: tuple[str, ...] = (
    "number",
    "title",
    "actor",
    "actors",
    "studio",
    "publisher",
    "series",
    "year",
    "release",
    "ext",
    "raw_dir",
    "raw_name",
    "cd?",
    "sub?",
    "mosaic?",
    "def?",
    "video_dir",
    "video_name",
    "video_relpath",
    "link_dir",
    "link_name",
    "raw_srt_name",
)

# 有闭合取值的占位符: 映射表的 key 必须是规范值, 否则写入 422. 未列入的占位符 (如 cd?) 不校验 key.
PLACEHOLDER_MAP_KEYS: dict[str, tuple[str, ...]] = {
    "mosaic?": MOSAIC_VALUES,
    "def?": DEFINITION_VALUES,
    "sub?": ("C",),
}


def normalize_link_template(value: str | None) -> str | None:
    """空白 link_template 视为未设置 (不创建链接)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# --- 可选组 DSL ---


@dataclass(frozen=True, slots=True)
class _Literal:
    text: str


@dataclass(frozen=True, slots=True)
class _Placeholder:
    name: str
    mapping: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _Group:
    children: tuple[_Node, ...]
    wrap: bool


type _Node = _Literal | _Placeholder | _Group


def _parse_placeholder_mapping(name: str, spec: str) -> tuple[tuple[str, str], ...]:
    """解析 `{name|k=v,k2=v2}` 的映射段. 空 spec / 缺 `=` / 空 key / 重复 key / 未知枚举 key 均拒绝."""
    if not spec.strip():
        raise ValueError("empty placeholder mapping in path template")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    allowed = PLACEHOLDER_MAP_KEYS.get(name)
    for item in spec.split(","):
        if "=" not in item:
            raise ValueError("invalid placeholder mapping in path template")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("empty mapping key in path template")
        if key in seen:
            raise ValueError(f"duplicate mapping key {key!r} in path template")
        if allowed is not None and key not in allowed:
            raise ValueError(f"unknown mapping key {key!r} for {{{name}}}")
        seen.add(key)
        pairs.append((key, value.strip()))
    return tuple(pairs)


class _Parser:
    """`{name}` 占位符 + 可选 `|k=v` 值映射 + `[...]` / `[[...]]` 可选组.

    `[...]` 组界不输出; 组内**直接**占位符全部为空串则整组丢弃, 有一个非空则渲染
    (空的那个输出空串). 嵌套组各自判断, 不并入外层.
    `[[...]]` 同样, 有值时把结果包一层 ``[]``.
    名字里的 ``?`` 只是标识符的一部分, 没有运算含义.
    `{name|原值=输出}` 在查出值之后替换; 空源值不走映射.
    """

    def __init__(self, src: str) -> None:
        self.src = src
        self.i = 0

    def parse(self) -> tuple[_Node, ...]:
        return tuple(self._parse_nodes(None))

    def _parse_nodes(self, closer: str | None) -> list[_Node]:
        nodes: list[_Node] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                nodes.append(_Literal("".join(buf)))
                buf.clear()

        while self.i < len(self.src):
            if closer is not None and self.src.startswith(closer, self.i):
                flush()
                self.i += len(closer)
                return nodes
            if self.src.startswith("[[", self.i):
                flush()
                self.i += 2
                nodes.append(_Group(tuple(self._parse_nodes("]]")), wrap=True))
                continue
            if self.src[self.i] == "[":
                flush()
                self.i += 1
                nodes.append(_Group(tuple(self._parse_nodes("]")), wrap=False))
                continue
            if self.src[self.i] == "{":
                flush()
                nodes.append(self._parse_placeholder())
                continue
            if self.src[self.i] == "]":
                raise ValueError("unmatched ] in path template")
            buf.append(self.src[self.i])
            self.i += 1
        flush()
        if closer is not None:
            raise ValueError("unclosed optional group in path template")
        return nodes

    def _parse_placeholder(self) -> _Placeholder:
        self.i += 1
        start = self.i
        while self.i < len(self.src) and self.src[self.i] != "}":
            if self.src[self.i] == "{":
                raise ValueError("nested braces in path template placeholder")
            self.i += 1
        if self.i >= len(self.src):
            raise ValueError("unclosed placeholder in path template")
        body = self.src[start : self.i]
        self.i += 1
        if not body:
            raise ValueError("empty placeholder in path template")
        name_part, sep, map_part = body.partition("|")
        name = name_part.strip()
        if not name:
            raise ValueError("empty placeholder in path template")
        mapping = _parse_placeholder_mapping(name, map_part) if sep else ()
        return _Placeholder(name, mapping)


def validate_path_template(value: str) -> str:
    """校验路径模板结构 (括号 / 占位符 / 值映射). 空串合法."""
    _Parser(value).parse()
    return value


PathTemplate = Annotated[str, AfterValidator(validate_path_template)]


def _direct_placeholders(nodes: Sequence[_Node]) -> tuple[_Placeholder, ...]:
    return tuple(node for node in nodes if isinstance(node, _Placeholder))


def _lookup(name: str, variables: dict[str, str]) -> str:
    if name in variables:
        return variables[name]
    return _UNKNOWN


def _resolve(node: _Placeholder, variables: dict[str, str]) -> str:
    """查出占位符值再按映射改写. 空串不走映射, 以便可选组省略未检出项."""
    raw = _lookup(node.name, variables)
    if raw == "":
        return ""
    mapped = dict(node.mapping)
    return mapped.get(raw, raw)


def _render_nodes(nodes: Sequence[_Node], variables: dict[str, str]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, _Literal):
            parts.append(node.text)
        elif isinstance(node, _Placeholder):
            parts.append(_resolve(node, variables))
        else:
            slots = _direct_placeholders(node.children)
            if slots and all(_resolve(item, variables) == "" for item in slots):
                continue
            inner = _render_nodes(node.children, variables)
            parts.append(f"[{inner}]" if node.wrap else inner)
    return "".join(parts)


def _template_keeps_absolute(template: str, variables: dict[str, str]) -> bool:
    """渲染后是否应保留绝对路径 (模板本身以 /、盘符、或绝对占位符开头)."""
    s = template.lstrip()
    if s.startswith(("/", "\\")):
        return True
    if _DRIVE.match(s):
        return True
    if s.startswith("{") and "}" in s:
        name = s[1 : s.index("}")].split("|", 1)[0].strip()
        val = variables.get(name, "")
        if val.startswith(("/", "\\")) or _DRIVE.match(val):
            return True
    return False


def _collapse_empty_segments(rendered: str, *, keep_absolute: bool) -> str:
    """丢掉空路径段, 避免空占位符把相对模板变成绝对路径."""
    posix = rendered.replace("\\", "/")
    parts = posix.split("/")
    drive = ""
    if parts and _DRIVE_ONLY.match(parts[0] or ""):
        drive = parts[0]
        parts = parts[1:]
    nonempty = [p for p in parts if p]
    if drive:
        return f"{drive}/{'/'.join(nonempty)}"
    joined = "/".join(nonempty)
    if keep_absolute:
        return f"/{joined}" if joined else "/"
    return joined


def render_path_template(template: str, variables: dict[str, str]) -> str:
    """渲染占位符与可选组, 并折叠空路径段."""
    rendered = _render_nodes(_Parser(template).parse(), variables)
    return _collapse_empty_segments(rendered, keep_absolute=_template_keeps_absolute(template, variables))


def render_content_template(template: str, variables: dict[str, str]) -> str:
    """填充占位符与可选组, 不折叠路径段. 供 strm 正文等非路径输出."""
    return _render_nodes(_Parser(template).parse(), variables)


def template_uses_placeholder(template: str, name: str) -> bool:
    """模板是否引用指定占位符 (含可选组内)."""
    return _nodes_use_placeholder(_Parser(template).parse(), name)


def _nodes_use_placeholder(nodes: Sequence[_Node], name: str) -> bool:
    for node in nodes:
        if isinstance(node, _Placeholder) and node.name == name:
            return True
        if isinstance(node, _Group) and _nodes_use_placeholder(node.children, name):
            return True
    return False


def _safe(value: str | None) -> str | None:
    """清理字符串以使其可安全用于文件路径."""
    if not value:
        return None
    return (
        value.replace("/", " ")
        .replace("\\", " ")
        .replace(":", " ")
        .replace("*", "")
        .replace("?", "")
        .replace('"', "")
        .replace("<", "")
        .replace(">", "")
        .replace("|", "")
        .strip()
    )


def _build_variables(
    metadata: Metadata,
    ext: str = "",
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    cd: int | None = None,
) -> dict[str, str]:
    """从元数据与源文件解析结果构建模板变量.

    可空 file 相位 (``cd?`` / ``sub?`` / ``mosaic?`` / ``def?``) 未检出时为空串,
    不是 Unknown. 未声明的 key 在渲染时仍回 Unknown.
    """
    year = metadata.release[:4] if metadata.release and len(metadata.release) >= 4 else None
    actor = metadata.actors[0] if metadata.actors else None
    source_dir = source_path.parent if source_path else None
    raw_dir = source_dir.name if source_dir else ""
    raw_name = source_path.stem if source_path else ""
    if cd is None and file_info is not None:
        cd = file_info.cd

    return {
        "number": metadata.number,
        "title": _safe(metadata.title) or metadata.number,
        "actor": _safe(actor) or _UNKNOWN,
        "actors": ", ".join(metadata.actors) if metadata.actors else _UNKNOWN,
        "studio": _safe(metadata.studio) or _UNKNOWN,
        "publisher": _safe(metadata.publisher) or _UNKNOWN,
        "series": _safe(metadata.series) or _UNKNOWN,
        "year": year or _UNKNOWN,
        "release": _safe(metadata.release) or _UNKNOWN,
        "ext": ext,
        "raw_dir": raw_dir,
        "raw_name": raw_name,
        "dir": raw_dir,
        "cd?": str(cd) if cd is not None else "",
        "sub?": "C" if file_info is not None and file_info.has_subtitle else "",
        "mosaic?": file_info.mosaic if file_info is not None and file_info.mosaic else "",
        "def?": file_info.definition if file_info is not None and file_info.definition else "",
    }


def _lexical_abs(path: Path) -> Path:
    """转为绝对路径并消除 ``.`` / ``..``, 但不跟随符号链接."""
    return Path(os.path.abspath(os.fspath(path)))  # noqa: PTH100


def video_relpath(dest: Path, library_root: Path) -> str:
    """dest 相对 library_root 的 POSIX 路径. dest 必须落在库根下."""
    dest_abs = _lexical_abs(dest)
    root_abs = _lexical_abs(library_root)
    try:
        rel = dest_abs.relative_to(root_abs)
    except ValueError as exc:
        raise ValueError(f"video dest '{dest_abs}' is outside library root '{root_abs}'") from exc
    return rel.as_posix()


def _video_relpath_or_empty(dest: Path, library_root: Path) -> str:
    try:
        return video_relpath(dest, library_root)
    except ValueError:
        return ""


def dest_template_variables(
    metadata: Metadata,
    dest: Path,
    library_root: Path,
    *,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    link: Path | None = None,
) -> dict[str, str]:
    """整理完成后的模板变量: metadata + 实际 dest 的 video_* / link_*."""
    variables = _build_variables(metadata, dest.suffix.lstrip("."), source_path, file_info)
    variables["video_dir"] = str(dest.parent)
    variables["video_name"] = dest.stem
    variables["video_relpath"] = _video_relpath_or_empty(dest, library_root)
    if link is not None:
        variables["link_dir"] = str(link.parent)
        variables["link_name"] = link.stem
    else:
        variables["link_dir"] = variables["video_dir"]
        variables["link_name"] = dest.stem
    return variables


def _render_template(
    template: str,
    variables: dict[str, str],
    base_path: Path,
    safe_dirs: Sequence[Path] | None,
) -> Path:
    """渲染模板并得到字面绝对路径, 强制约束在允许的边界内.

     边界规则:
    - 相对路径模板: 相对 base_path 解析, resolve 后必须是 base_path 的后代 (ALLOW_ALL 也不例外).
    - 绝对路径模板: resolve 后必须位于 base_path 或 safe_dirs 任一目录之下.

     渲染时用字面路径, 仅折叠 ``.`` / ``..`` 不跟随链接.

     Raises:
         ValueError: 渲染结果逃逸了允许边界
    """
    rendered = render_path_template(template, variables)
    path = Path(rendered)
    if path.is_absolute():
        candidate = _lexical_abs(path)
        if safe_dirs is None:
            return candidate
        allowed_roots = [base_path, *safe_dirs]
        if not is_any_descendant(candidate, *allowed_roots):
            followed = candidate.resolve()
            raise ValueError(
                f"Path traversal detected: rendered path '{followed}' escapes base '{base_path}' and safe directories"
            )
        return candidate
    candidate = _lexical_abs(base_path / path)
    if not is_descendant(candidate, base_path):
        followed = candidate.resolve()
        raise ValueError(f"Path traversal detected: rendered path '{followed}' escapes base '{base_path}'")
    return candidate


def resolve_paths(
    library: Library,
    metadata: Metadata,
    ext: str = "",
    cd: int | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
) -> ResolvedPaths:
    """根据 Library 模板配置和元数据渲染所有输出路径.

    Args:
        library: 媒体库配置 (含模板字段)
        metadata: 元数据对象 (需有 number, title, actors, studio 等属性)
        ext: 原始文件扩展名 (不含点, 如 "mp4", "mkv")
        cd: CD/分片编号; None 时回退 file_info.cd. 写入 ``{cd?}``, 由模板可选组决定是否出现在路径中.
        source_path: 源文件完整路径, 提供 {raw_dir} (源父目录名) 与 {raw_name} (源文件名不含扩展名)
        file_info: 源文件解析结果 (parse_file_info), 提供 {mosaic?} / {def?} / {sub?} / {cd?}
        safe_dirs: 允许绝对路径模板落地的可信目录集 (多盘分存等). base_path 始终可信, 无需重复列出.
            ``None`` 表示不限制绝对模板落点 (相对模板仍须在 base_path 下).

    Returns:
        ResolvedPaths 包含视频与刮削产物路径 (字幕按源文件逐条渲染, 见 resolve_subtitle_path).

    Raises:
        ValueError: 任一模板渲染后逃逸了 base_path 与 safe_dirs 构成的边界
    """
    base_path = Path(library.path)
    variables = _build_variables(metadata, ext, source_path, file_info, cd)

    video = _render_template(library.video_template, variables, base_path, safe_dirs)

    video_name = video.stem
    variables["video_dir"] = str(video.parent)
    variables["video_name"] = video_name
    variables["video_relpath"] = _video_relpath_or_empty(video, base_path)
    link = _resolve_link_path(library, variables, base_path, safe_dirs)
    if link is not None:
        variables["link_dir"] = str(link.parent)
        variables["link_name"] = link.stem
    else:
        variables["link_dir"] = variables["video_dir"]
        variables["link_name"] = video_name

    thumb = _render_template(library.thumb_template or THUMB_TEMPLATE_DEFAULT, variables, base_path, safe_dirs)
    poster = _render_template(library.poster_template or POSTER_TEMPLATE_DEFAULT, variables, base_path, safe_dirs)
    fanart = _render_template(library.fanart_template or FANART_TEMPLATE_DEFAULT, variables, base_path, safe_dirs)
    extrafanart_dir = _render_template(
        library.extrafanart_template or EXTRAFANART_TEMPLATE_DEFAULT, variables, base_path, safe_dirs
    )
    nfo = _render_template(library.nfo_template or NFO_TEMPLATE_DEFAULT, variables, base_path, safe_dirs)
    trailer = _render_template(library.trailer_template or TRAILER_TEMPLATE_DEFAULT, variables, base_path, safe_dirs)

    return ResolvedPaths(
        video=video,
        thumb=thumb,
        poster=poster,
        fanart=fanart,
        extrafanart_dir=extrafanart_dir,
        nfo=nfo,
        trailer=trailer,
        link=link,
    )


def _resolve_link_path(
    library: Library,
    variables: dict[str, str],
    base_path: Path,
    safe_dirs: Sequence[Path] | None,
) -> Path | None:
    """渲染 link_template; 空模板返回 None. 结果必须落在库根之外.

    此时 `{video_dir}` / `{video_name}` / `{video_relpath}` 已注入, `{link_dir}` / `{link_name}` 尚未注入.
    """
    template = normalize_link_template(library.link_template)
    if template is None:
        return None
    link = _render_template(template, variables, base_path, safe_dirs)
    if library.link_mode == LinkMode.STRM:
        link = link.with_suffix(".strm")
    if is_descendant(link, base_path):
        raise ValueError(f"link_template must resolve outside the library root: '{link}' is under '{base_path}'")
    return link


def resolve_subtitle_path(
    library: Library,
    metadata: Metadata,
    subtitle_source: Path,
    *,
    video_dir: Path,
    link_dir: Path | None = None,
    video_name: str = "",
    video_dest: Path | None = None,
    link_name: str | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
) -> Path:
    """按字幕模板渲染单个字幕的目标路径.

    `{ext}` / `{raw_srt_name}` 取自该字幕源文件; `{raw_name}` / `{raw_dir}` 仍是视频源.
    `{video_dir}` / `{video_name}` 为整理后视频父目录与文件名 (不含扩展名);
    `{video_relpath}` 为整理后视频相对库根的路径 (`video_dest` 未传时为空);
    `{link_dir}` / `{link_name}` 为链接父目录与文件名 (未设链接时分别与视频侧相同).
    默认模板保持原文件名与扩展名.
    """
    base_path = Path(library.path)
    variables = _build_variables(
        metadata, ext=subtitle_source.suffix.lstrip("."), source_path=source_path, file_info=file_info
    )
    variables["video_dir"] = str(video_dir)
    variables["video_name"] = video_name
    variables["video_relpath"] = _video_relpath_or_empty(video_dest, base_path) if video_dest is not None else ""
    variables["link_dir"] = str(link_dir if link_dir is not None else video_dir)
    variables["link_name"] = link_name if link_name is not None else video_name
    variables["raw_srt_name"] = subtitle_source.stem
    template = library.subtitle_template or SUBTITLE_TEMPLATE_DEFAULT
    return _render_template(template, variables, base_path, safe_dirs)
