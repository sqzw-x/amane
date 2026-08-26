"""在 parse_filename 基础上提取整理用附加字段 (CD / 字幕 / 马赛克 / 分辨率)."""

import re
from dataclasses import dataclass
from pathlib import Path

from .number import parse_filename


@dataclass(frozen=True)
class FileInfo:
    number: str
    content_type: str
    prefix: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: str | None = None
    definition: str | None = None


def parse_file_info(filepath: str | Path) -> FileInfo:
    """
    将媒体文件路径解析为结构化信息.

    提取: 番号, 内容类型, CD 分片, 字幕标记, 马赛克类型, 分辨率.
    """
    filepath = str(filepath)
    parsed = parse_filename(filepath)
    basename = Path(filepath).stem.upper()

    cd = _detect_cd(basename)
    has_subtitle = _detect_subtitle(basename)
    mosaic = _detect_mosaic(basename)
    definition = _detect_definition(basename)

    return FileInfo(
        number=parsed.number,
        content_type=parsed.content_type.value,
        prefix=parsed.prefix,
        cd=cd,
        has_subtitle=has_subtitle,
        mosaic=mosaic,
        definition=definition,
    )


def _detect_cd(basename: str) -> int | None:
    """从文件名中检测分集编号 (多盘/多段, 如 -CD1 / -PART2 / -A / -1)."""
    if m := re.search(r"[-_.]CD(\d{1,2})", basename):
        return int(m[1])
    if m := re.search(r"[-_.]PART(\d{1,2})", basename):
        return int(m[1])
    if m := re.search(r"-([AB])(?:\.|$)", basename):
        return ord(m[1]) - ord("A") + 1
    # 裸数字分集: 文件名以 -1..-9 结尾 (如 MIDV-123-1.mp4); -0 无意义, 零填充/两位尾数 (如 -01 / -10)
    # 会与合法番号 (如 ABC-12) 撞车, 均不识别.
    if m := re.search(r"-([1-9])$", basename):
        return int(m[1])
    return None


def _detect_subtitle(basename: str) -> bool:
    """通过文件名标记检测是否包含字幕."""
    if re.search(r"-U?C(?:\.|$|-CD)", basename):
        return True
    return bool(re.search(r"[字幕中文]", basename))


def _detect_mosaic(basename: str) -> str | None:
    """检测马赛克/审查类型."""
    if re.search(r"無碼|无码|UNCENSORED", basename):
        return "uncensored"
    if re.search(r"破解|流出|LEAKED", basename):
        return "cracked"
    if re.search(r"-UC(?:\.|$)", basename):
        return "uncensored"
    return None


# 分辨率标记: 元组顺序即优先级 (高 → 低), 同时命中多个时取靠前者; 2160p 归一化为 4K.
# \b 边界确保不误中番号/前缀里的字母串 (如 SKYHD-xxx 不命中 HD, 4KS/HDTV 不命中 4K/HD).
_DEFINITION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("8K", re.compile(r"\b8K\b")),
    ("4K", re.compile(r"\b4K\b")),
    ("4K", re.compile(r"\b2160P\b")),
    ("1440p", re.compile(r"\b1440P\b")),
    ("1080p", re.compile(r"\b1080P\b")),
    ("720p", re.compile(r"\b720P\b")),
    ("480p", re.compile(r"\b480P\b")),
    ("HD", re.compile(r"\bHD\b")),
    ("SD", re.compile(r"\bSD\b")),
)


def _detect_definition(basename: str) -> str | None:
    """从文件名检测分辨率标记.

    作用于原始 basename, 独立于番号提取 (后者会剥除分辨率标记), 与 CD 检测同一约定:
    ORGANIZE 时检测一次, 不落库. 无命中返回 None; 命中多个时取最高.
    """
    for value, pattern in _DEFINITION_MARKERS:
        if pattern.search(basename):
            return value
    return None
