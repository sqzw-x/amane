"""媒体文件扩展名与跳过/黑名单正则 (目录监控与扫描共用).

叶子模块: 无任何 amane 内部导入, 供 scheduler 与 handlers 两侧引用,
避免 handlers._common ↔ scheduler.watcher 的循环导入.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

if TYPE_CHECKING:
    from re import Pattern

MEDIA_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".wmv",
        ".flv",
        ".mov",
        ".ts",
        ".iso",
        ".strm",
    }
)

# 与默认 trailer 模板文件名 `{link_dir}/trailer.mp4` 对齐; 空串表示不跳过.
DEFAULT_TRAILER_PATTERN = "(?i)trailer"

# ORGANIZE 同目录字幕发现用; 空列表关闭. 不进 MEDIA_EXTENSIONS (字幕不是正片).
DEFAULT_SUBTITLE_EXTENSIONS: tuple[str, ...] = (".srt", ".ass", ".ssa", ".vtt", ".sub")

_SUBTITLE_EXT_RE = re.compile(r"^\.[a-z0-9]+$")

# 黑名单命中的文件在 ORGANIZE 时移入库根下该目录 (固定保留名, 任何深度都不入库).
TRASH_DIRNAME = ".amane_trash"

# .strm 在扫描扩展名里 (当正片入口), 但是路径指针不是视频字节; 体积过滤不碰它.
_POINTER_EXTENSIONS = frozenset({".strm"})


def compile_skip_patterns(patterns: Sequence[str | None] | None) -> list[Pattern[str]] | None:
    """逐条编译跳过正则 (预告片 + 黑名单), 任一命中即跳过.

    - 空列表/全是空串返回 None (不跳过)
    - 非法模式防御性跳过该项 (写入时已校验, 见 validate_regex_pattern)
    - 逐条编译而非 `|` 拼接: 用户书写全局旗标 (如 `(?i)ads`) 在拼接中间会触发
      "global flags not at the start of the expression" 而整体编译失败.
    """
    compiled: list[Pattern[str]] = []
    for p in patterns or []:
        if not p or not p.strip():
            continue
        try:
            compiled.append(re.compile(p))
        except re.error:
            continue
    return compiled or None


def compile_skip_pattern(pattern: str | None) -> list[Pattern[str]] | None:
    """编译单个跳过正则 (等价 compile_skip_patterns([pattern]))."""
    return compile_skip_patterns([pattern])


def validate_regex_pattern(pattern: str, field: str) -> str:
    """校验用户输入的正则; 空串合法 (关闭)."""
    if pattern.strip():
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid {field}: {exc}") from exc
    return pattern


def validate_trailer_pattern(pattern: str) -> str:
    """校验预告片正则; 空串合法 (关闭跳过)."""
    return validate_regex_pattern(pattern, "trailer_pattern")


def validate_blacklist_pattern(pattern: str) -> str:
    """校验黑名单正则; 空串合法 (该项为空, 无效果)."""
    return validate_regex_pattern(pattern, "blacklist_pattern")


def validate_subtitle_extension(value: str) -> str:
    """规范化字幕扩展名: 小写、补前导点. 空串/路径分隔符/非字母数字非法."""
    stripped = value.strip().lower()
    if not stripped:
        raise ValueError("subtitle extension must not be empty")
    if not stripped.startswith("."):
        stripped = f".{stripped}"
    if "/" in stripped or "\\" in stripped or _SUBTITLE_EXT_RE.fullmatch(stripped) is None:
        raise ValueError(f"invalid subtitle extension: {value!r}")
    return stripped


def normalize_subtitle_extensions(values: list[str]) -> list[str]:
    """逐项规范化并去重 (保序). 空列表合法, 表示关闭字幕发现."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        ext = validate_subtitle_extension(raw)
        if ext not in seen:
            seen.add(ext)
            out.append(ext)
    return out


def validate_min_file_size(value: int) -> int:
    """字节阈值; 0 关闭. 负数非法."""
    if value < 0:
        raise ValueError("min_file_size must be >= 0")
    return value


TrailerPattern = Annotated[str, AfterValidator(validate_trailer_pattern)]
BlacklistPattern = Annotated[str, AfterValidator(validate_blacklist_pattern)]
SubtitleExtensions = Annotated[list[str], AfterValidator(normalize_subtitle_extensions)]
MinFileSize = Annotated[int, AfterValidator(validate_min_file_size)]


def is_video_media(path: Path, media_extensions: frozenset[str] | None = None) -> bool:
    """后缀是否属于扫描用的视频扩展名白名单 (默认 MEDIA_EXTENSIONS)."""
    extensions = MEDIA_EXTENSIONS if media_extensions is None else media_extensions
    return path.suffix.lower() in extensions


def is_undersized_video(
    path: Path,
    min_file_size: int,
    media_extensions: frozenset[str] | None = None,
) -> bool:
    """是否为低于阈值的视频文件.

    - min_file_size <= 0 视为关闭.
    - 只对扫描视频扩展名判定; 图片 / nfo / 字幕等后缀一律不算.
    - .strm 是路径指针, 体积无意义, 不参与过滤.
    - 软链接跟随目标, 比目标文件字节, 不是链接节点本身.
    - stat 失败 (含悬空链接) 视为不匹配, 避免把读不到的正片当广告丢掉.
    """
    if min_file_size <= 0:
        return False
    suffix = path.suffix.lower()
    if suffix in _POINTER_EXTENSIONS:
        return False
    if not is_video_media(path, media_extensions):
        return False
    try:
        return path.stat(follow_symlinks=True).st_size < min_file_size
    except OSError:
        return False


def is_skipped_media(path: Path, pattern: str | None) -> bool:
    """文件名 (含扩展名) 命中正则则为跳过文件 (预告片/黑名单), 扫描/监控应跳过."""
    compiled = compile_skip_patterns([pattern])
    if compiled is None:
        return False
    return any(r.search(path.name) for r in compiled)


def is_in_trash(path: Path) -> bool:
    """路径任一深度组件为 `.amane_trash` 则视为回收站内容: 不入库、不触发监控事件."""
    return any(part == TRASH_DIRNAME for part in path.parts)
