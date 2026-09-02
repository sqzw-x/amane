"""模板语言: Parser → 树; TemplateContext 按相位组装; Engine.render 填树后 Clean.

PathEngine 折叠空段并约束落点. StrmEngine 不折叠 (保留 `https://`), 不检查 safe_dirs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..parsing.file_info import DEFINITION_VALUES, MOSAIC_VALUES, FileInfo
from ..utils.path import is_any_descendant, is_descendant

if TYPE_CHECKING:
    from ..db.models import Metadata

_UNKNOWN = "Unknown"
_DRIVE = re.compile(r"^[A-Za-z]:")
_DRIVE_ONLY = re.compile(r"^[A-Za-z]:$")

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


class Parser:
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


def _build_variables(
    metadata: Metadata,
    ext: str = "",
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    cd: int | None = None,
) -> dict[str, str]:
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


@dataclass
class TemplateContext:
    """占位符取值. 相位顺序: metadata/file → apply_video → apply_link → (字幕再改 ext / raw_srt_name)."""

    variables: dict[str, str]
    library_root: Path | None = None
    dest: Path | None = None

    @classmethod
    def from_mapping(cls, variables: dict[str, str]) -> TemplateContext:
        return cls(variables=variables)

    @classmethod
    def from_metadata(
        cls,
        metadata: Metadata,
        *,
        ext: str = "",
        source_path: Path | None = None,
        file_info: FileInfo | None = None,
        cd: int | None = None,
    ) -> TemplateContext:
        return cls(variables=_build_variables(metadata, ext, source_path, file_info, cd))

    def apply_video(self, dest: Path, library_root: Path) -> None:
        self.dest = dest
        self.library_root = library_root
        self.variables["video_dir"] = str(dest.parent)
        self.variables["video_name"] = dest.stem
        self.variables["video_relpath"] = _video_relpath_or_empty(dest, library_root)

    def apply_link(self, link: Path | None) -> None:
        if link is not None:
            self.variables["link_dir"] = str(link.parent)
            self.variables["link_name"] = link.stem
            return
        self.variables["link_dir"] = self.variables.get("video_dir", "")
        self.variables["link_name"] = self.variables.get("video_name", "")

    def apply_subtitle(self, subtitle_source: Path) -> None:
        self.variables["ext"] = subtitle_source.suffix.lstrip(".")
        self.variables["raw_srt_name"] = subtitle_source.stem


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


class TemplateEngine:
    """解析一次, render 填树后交给 clean."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tree = Parser(source).parse()

    def uses(self, name: str) -> bool:
        return _nodes_use_placeholder(self.tree, name)

    def fill(self, ctx: TemplateContext) -> str:
        return _render_nodes(self.tree, ctx.variables)

    def clean(self, filled: str, ctx: TemplateContext) -> str:
        return filled

    def render(self, ctx: TemplateContext) -> str:
        return self.clean(self.fill(ctx), ctx)


class PathEngine(TemplateEngine):
    """路径输出: 折叠空段, 再按库根 / safe_dirs 落成字面绝对路径."""

    def clean(self, filled: str, ctx: TemplateContext) -> str:
        return _collapse_empty_segments(filled, keep_absolute=_template_keeps_absolute(self.source, ctx.variables))

    def resolve(self, ctx: TemplateContext, base_path: Path, safe_dirs: Sequence[Path] | None) -> Path:
        """渲染并得到字面绝对路径, 强制约束在允许的边界内.

        边界规则:
        - 相对路径模板: 相对 base_path 解析, 必须是 base_path 的后代 (ALLOW_ALL 也不例外).
        - 绝对路径模板: 必须位于 base_path 或 safe_dirs 任一目录之下.

        渲染时用字面路径, 仅折叠 ``.`` / ``..`` 不跟随链接.
        """
        rendered = self.render(ctx)
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


class StrmEngine(TemplateEngine):
    """STRM 正文: 不折叠空段. 引用 `{video_relpath}` 时 dest 必须在库根下."""

    def clean(self, filled: str, ctx: TemplateContext) -> str:
        return filled if filled.endswith("\n") else f"{filled}\n"

    def render(self, ctx: TemplateContext) -> str:
        if self.uses("video_relpath"):
            if ctx.dest is None or ctx.library_root is None:
                raise ValueError("video_relpath requires dest and library_root")
            video_relpath(ctx.dest, ctx.library_root)
        return super().render(ctx)
