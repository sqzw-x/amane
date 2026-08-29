"""从路径或自由文本解析番号、内容类型, 以及文件相位标记.

核心是 ``parse_file_info``: 有路径就走路径 (目录段可补番号、关键词可改类型);
只有字符串就当自由文本 (RSS 标题、已有番号), 未命中则 ``number is None``, 不把原文冒充番号.
两者都返回 ``FileInfo``. 常用字段投影 (``extract_number`` / ``infer_content_type`` 等) 是同一函数的包装.

文件相位 (cd / 字幕 / 马赛克 / 清晰度) 与类型正交, 只看文件名; 马赛克还可从目录整段补.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ContentType(StrEnum):
    CENSORED = "censored"
    UNCENSORED = "uncensored"
    CHINESE = "chinese"
    WESTERN = "western"
    FC2 = "fc2"
    AMATEUR = "amateur"
    HENTAI = "hentai"


class Mosaic(StrEnum):
    UNCENSORED = "uncensored"
    CRACKED = "cracked"
    LEAKED = "leaked"


@dataclass(frozen=True)
class FileInfo:
    number: str | None
    content_type: ContentType
    prefix: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: Mosaic | None = None
    definition: str | None = None


class FilePhase(TypedDict):
    """MediaFile 上持久化的文件相位 (path 的投影, 不含 number/cd)."""

    content_type: ContentType
    mosaic: Mosaic | None
    has_subtitle: bool
    definition: str | None


@dataclass(frozen=True)
class FilePhaseSummary:
    """多文件相位聚合: 任一文件具备某性质即亮; definition 取最高档."""

    has_subtitle: bool = False
    uncensored: bool = False
    mosaics: tuple[Mosaic, ...] = ()
    definition: str | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UNCENSORED_PREFIXES = (
    "BT-",
    "CT-",
    "EMP-",
    "CCDV-",
    "CWP-",
    "CWPBD-",
    "DSAM-",
    "DRC-",
    "DRG-",
    "GACHI-",
    "HEYDOUGA",
    "JAV-",
    "LAF-",
    "LAFBD-",
    "HEYZO-",
    "KTG-",
    "KP-",
    "KG-",
    "LLDV-",
    "MCDV-",
    "MKD-",
    "MKBD-",
    "MMDV-",
    "NIP-",
    "PB-",
    "PT-",
    "QE-",
    "RED-",
    "RHJ-",
    "S2M-",
    "SKY-",
    "SKYHD-",
    "SMD-",
    "SSDV-",
    "SSKP-",
    "TRG-",
    "TS-",
    "XXX-AV-",
    "YKB-",
    "BIRD",
    "BOUGA",
    "H4610-",
    "C0930-",
    "H0930-",
    "S2M",
    "MCB3D",
)

_SUREN_PREFIXES: dict[str, str] = {
    "LUXU-": "259LUXU-",
    "ARA-": "200GANA-",
    "MIUM-": "300MIUM-",
    "NTK-": "300NTK-",
    "MAAN-": "300MAAN-",
}

_WESTERN_NAMES: dict[str, str] = {
    "vixen": "Vixen",
    "blacked": "Blacked",
    "tushy": "Tushy",
    "sexart": "SexArt",
    "x-art": "X-Art",
    "nubilefilms": "NubileFilms",
    "babes": "Babes",
    "wgp": "WhenGirlsPlay",
    "twistys": "Twistys",
    "deeper": "Deeper",
    "slayed": "Slayed",
    "tushy raw": "TushyRaw",
    "blackedraw": "BlackedRaw",
}

_CATALOG_NUMBER = re.compile(r"[A-Z]{2,}-\d{2,}[Z]?")
_DMM_CONCAT = re.compile(r"([A-Z]{2,})00(\d{3})")
_WESTERN_PROBE = re.compile(r"([A-Z0-9_]{2,})[-.]2?0?(\d{2}[-.]\d{2}[-.]\d{2})")
_WESTERN_PARSE = re.compile(r"([A-Z0-9-]{2,})[-_.]2?0?(\d{2}[-.]\d{2}[-.]\d{2})")

_ESCAPE_MARKERS = (
    "4K",
    "4KS",
    "8K",
    "HD",
    "LR",
    "VR",
    "DVD",
    "FULL",
    "HEVC",
    "H264",
    "H265",
    "X264",
    "X265",
    "AAC",
    "XXX",
    "PRT",
)

_ROOT_PARTS = frozenset({"/", ".", ""})
_CD_DIR = re.compile(r"^(?:CD|PART)(\d{1,2})$", re.IGNORECASE)
_DIR_BRACKET_STRIP = "[]【】()（）"

_MOSAIC_DIR_TOKENS: dict[str, Mosaic] = {
    k.casefold(): v
    for k, v in (
        ("uncensored", Mosaic.UNCENSORED),
        ("無碼", Mosaic.UNCENSORED),
        ("无码", Mosaic.UNCENSORED),
        ("cracked", Mosaic.CRACKED),
        ("破解", Mosaic.CRACKED),
        ("leaked", Mosaic.LEAKED),
        ("流出", Mosaic.LEAKED),
    )
}
MOSAIC_VALUES: tuple[Mosaic, ...] = tuple(Mosaic)

# 分辨率: 元组顺序即优先级; 2160p 归一为 4K.
# 匹配作用于 _normalize_markers 之后的 basename:
# - 数字标记允许紧跟帧率 (1080p60); K/p 后不得再接字母 (4KS / HDTV 不命中)
# - HD/SD 不得是番号前缀: HD-123 / HD_123 (下划线归一成点后同形) 不命中
_DEFINITION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("8K", re.compile(r"\b8K(?:\d+)?\b")),
    ("4K", re.compile(r"\b4K(?:\d+)?\b")),
    ("4K", re.compile(r"\b2160P(?:\d+)?\b")),
    ("1440p", re.compile(r"\b1440P(?:\d+)?\b")),
    ("1080p", re.compile(r"\b1080P(?:\d+)?\b")),
    ("720p", re.compile(r"\b720P(?:\d+)?\b")),
    ("480p", re.compile(r"\b480P(?:\d+)?\b")),
    ("HD", re.compile(r"\bHD\b(?![.-]\d)")),
    ("SD", re.compile(r"\bSD\b(?![.-]\d)")),
)
DEFINITION_VALUES: tuple[str, ...] = tuple(dict.fromkeys(value for value, _ in _DEFINITION_MARKERS))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_file_info(
    path: str | Path | None = None,
    *,
    text: str | None = None,
    escape_strings: list[str] | None = None,
) -> FileInfo:
    """有路径解析路径, 否则解析 ``text``. 至少要有一个.

    路径: 文件名优先, 未命中可回退父目录; 仍未命中用清理后的文件名 (不丢).
    目录关键词 (欧美 / 里番 / getchu) 可盖掉类型. 分集还可认直接父目录 CD/PART.
    文本: 只跑番号规则, 未命中 ``number is None`` (不把原文当番号). 文件相位只看该字符串
    (``Path(text).stem``, 与字幕分集检测一致), 不看目录.
    """
    if path is None and text is None:
        raise ValueError("path or text required")
    escape = escape_strings or []
    if path is not None:
        p = Path(path)
        stem = p.stem
        dirs = _dir_names(p)
        number, content_type = _identify(stem.strip(), dirs, escape, fallback=True)
        content_type = _classify_from_path(stem, dirs) or content_type
        basename = stem.upper()
        cd = _detect_cd(basename)
        if cd is None and dirs:
            cd = _detect_cd_from_parent(dirs[-1])
        mosaic = _detect_mosaic(basename) or _detect_mosaic_from_dirs(dirs)
    else:
        assert text is not None
        dirs = ()
        number, content_type = _identify(text, dirs, escape, fallback=False)
        basename = Path(text).stem.upper()
        cd = _detect_cd(basename)
        mosaic = _detect_mosaic(basename)
    return FileInfo(
        number=number,
        content_type=content_type,
        prefix=_prefix(number) if number else "",
        cd=cd,
        has_subtitle=_detect_subtitle(basename),
        mosaic=mosaic,
        definition=_detect_definition(basename),
    )


def extract_number(text: str, escape_strings: list[str] | None = None) -> str | None:
    """从自由文本提取番号. 未命中返回 None, 不把原文冒充番号."""
    return parse_file_info(text=text, escape_strings=escape_strings).number


def infer_content_type(number: str, file_path: str | None = None) -> ContentType:
    """有挂载文件按路径, 否则按番号; 未命中已知形态则欧美."""
    return parse_file_info(file_path, text=number).content_type


def detect_cd(filename: str | Path) -> int | None:
    """从文件名 (不含目录) 检测分集. 字幕配对只走这一层."""
    return parse_file_info(text=str(filename)).cd


def get_prefix(number: str) -> str:
    """番号字母前缀 (MIDV-123 → MIDV)."""
    return parse_file_info(text=number).prefix


def is_uncensored(number: str) -> bool:
    return infer_content_type(number) == ContentType.UNCENSORED


def is_amateur(number: str) -> bool:
    return infer_content_type(number) == ContentType.AMATEUR


def file_phase_from_path(path: str | Path) -> FilePhase:
    """从路径抽出要落库的文件相位列."""
    info = parse_file_info(path)
    return FilePhase(
        content_type=info.content_type,
        mosaic=info.mosaic,
        has_subtitle=info.has_subtitle,
        definition=info.definition,
    )


def file_shows_uncensored(mosaic: Mosaic | None, content_type: ContentType | str) -> bool:
    """无码角标/筛选: 文件名马赛克标记或片种为无码 (HEYZO 等不必带 -U)."""
    return mosaic == Mosaic.UNCENSORED or content_type == ContentType.UNCENSORED


def max_definition(values: Iterable[str | None]) -> str | None:
    """按 DEFINITION_VALUES 顺序取最高档; 未知值忽略."""
    rank = {name: index for index, name in enumerate(DEFINITION_VALUES)}
    best: str | None = None
    best_rank = len(DEFINITION_VALUES)
    for value in values:
        if value is None:
            continue
        current = rank.get(value)
        if current is not None and current < best_rank:
            best = value
            best_rank = current
    return best


def summarize_file_phases(phases: Iterable[FilePhase]) -> FilePhaseSummary:
    """按「任一文件具备」聚合; mosaics 按 MOSAIC_VALUES 去重保序."""
    items = list(phases)
    if not items:
        return FilePhaseSummary()
    mosaic_present = {phase["mosaic"] for phase in items if phase["mosaic"] is not None}
    return FilePhaseSummary(
        has_subtitle=any(phase["has_subtitle"] for phase in items),
        uncensored=any(file_shows_uncensored(phase["mosaic"], phase["content_type"]) for phase in items),
        mosaics=tuple(mosaic for mosaic in MOSAIC_VALUES if mosaic in mosaic_present),
        definition=max_definition(phase["definition"] for phase in items),
    )


# ---------------------------------------------------------------------------
# Identify number + type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    """同一输入的三个加工阶段.

    escaped: 只去掉分辨率/用户逃逸串. 国产 MD 走这里 — 后面剥尾部盘符会把 MD0165-1 的 -1 吃掉.
    dated: 再剥 CD/字幕尾符, 日期还在. 欧美 studio.YY.MM.DD 用这个.
    catalog: 再剥日期并归一 FC2. 其余番号规则用这个.
    """

    escaped: str
    dated: str
    catalog: str


def _prepare(basename: str, escape_strings: list[str]) -> _Prepared:
    real_name = basename.strip() + "."
    escaped = _remove_escape_strings(real_name, escape_strings) + "."
    dated = (
        escaped.replace("-C.", ".")
        .replace(".PART", "-CD")
        .replace("-PART", "-CD")
        .replace(" EP.", ".EP")
        .replace("-CD-", "")
    )
    dated = re.sub(r"[-_ .]CD\d{1,2}", "", dated)
    dated = re.sub(r"[-_ .][A-Z0-9]\.$", "", dated)
    dated = dated.replace(" ", "-").strip("-_. ")
    catalog = re.sub(r"\d{4}[-_.]\d{1,2}[-_.]\d{1,2}", "", dated)
    catalog = re.sub(r"[-\[]\d{2}[-_.]\d{2}[-_.]\d{2}]?", "", catalog)
    catalog = (
        catalog.replace("FC2-PPV", "FC2-").replace("FC2PPV", "FC2-").replace("--", "-").replace("GACHIPPV", "GACHI")
    )
    return _Prepared(escaped=escaped, dated=dated, catalog=catalog)


def _remove_escape_strings(filename: str, escape_strings: list[str]) -> str:
    upper = filename.upper()
    for string in escape_strings:
        if string:
            upper = upper.replace(string.upper(), "")
    for marker in _ESCAPE_MARKERS:
        upper = re.sub(rf"[-_ .\[]{marker}[-_ .\]]", "-", upper)
    return upper.replace("--", "-").strip("-_ .")


def _identify(
    stem: str,
    dirs: tuple[str, ...],
    escape: list[str],
    *,
    fallback: bool,
) -> tuple[str | None, ContentType]:
    """文件名 → 父目录 (目录不用过宽拼接) → 可选的文件名回退."""
    if found := _match(stem, escape, generic=True):
        return found
    for name in reversed(dirs):
        if found := _match(name, escape, generic=False):
            return found
    if not fallback:
        return None, ContentType.WESTERN
    return _fallback(stem, escape)


def _match(basename: str, escape_strings: list[str], *, generic: bool) -> tuple[str, ContentType] | None:
    """命中已知形态则 (番号, 类型), 否则 None. 不含文件名回退."""
    t = _prepare(basename, escape_strings)
    c, escaped, dated = t.catalog, t.escaped, t.dated

    if "MYWIFE" in c and re.search(r"NO\.\d*", c):
        return f"Mywife No.{re.findall(r'NO\.(\d*)', c)[0]}", ContentType.CENSORED
    if m := re.search(r"CW3D2D?BD-?\d{2,}", c):
        return m.group(), _type_of_catalog_id(m.group())
    if m := re.search(r"MMR-?[A-Z]{2,}-?\d+[A-Z]*", c):
        number = m.group().replace("MMR-", "MMR")
        return number, _type_of_catalog_id(number)
    if (m := re.search(r"([^A-Z]|^)(MD[A-Z-]*\d{4,}(-\d)?)", escaped)) and "MDVR" not in escaped:
        return m.group(2), ContentType.CHINESE
    if _WESTERN_PROBE.findall(dated):
        result = _WESTERN_PARSE.findall(dated)
        if result:
            short_name = result[0][0].strip("-").lower()
            full_name: str = _WESTERN_NAMES.get(short_name) or short_name
            number = (
                full_name.lower().replace("-", "").replace(".", "") + "." + result[0][1].replace("-", ".")
            ).capitalize()
            return number, ContentType.WESTERN
    if m := re.search(r"XXX-AV-\d{4,}", c):
        return m.group(), ContentType.UNCENSORED
    if m := re.search(r"MKY-[A-Z]+-\d{3,}", c):
        return m.group(), ContentType.CHINESE
    if "FC2" in c:
        fc2 = c.replace("PPV", "").replace("_", "-").replace("--", "-")
        if m := re.search(r"FC2-\d{5,}", fc2):
            return m.group(), ContentType.FC2
        if m := re.search(r"FC2\d{5,}", fc2):
            return m.group().replace("FC2", "FC2-"), ContentType.FC2
    if "HEYZO" in c:
        hz = c.replace("_", "-").replace("--", "-")
        if m := re.search(r"HEYZO-\d{3,}", hz):
            return m.group(), ContentType.UNCENSORED
        if m := re.search(r"HEYZO\d{3,}", hz):
            return m.group().replace("HEYZO", "HEYZO-"), ContentType.UNCENSORED
    if m := re.search(r"(H4610|C0930|H0930)-[A-Z]+\d{4,}", c):
        return m.group(), ContentType.UNCENSORED
    if m := re.search(r"KIN8(TENGOKU)?-?\d{3,}", c):
        return m.group().replace("TENGOKU", "-").replace("--", "-"), ContentType.UNCENSORED
    if (m := re.search(r"S2M[BD]*-\d{3,}", c)) or (m := re.search(r"MCB3D[BD]*-\d{2,}", c)):
        return m.group(), ContentType.UNCENSORED
    if m := re.search(r"T28-?\d{3,}", c):
        return m.group().replace("T2800", "T28-"), ContentType.CENSORED
    if m := re.search(r"TH101-\d{3,}-\d{5,}", c):
        return m.group().lower(), ContentType.UNCENSORED
    if m := _DMM_CONCAT.search(c):
        number = f"{m[1]}-{m[2]}"
        return number, _type_of_catalog_id(number)
    if m := re.search(r"\d{2,}[A-Z]{2,}-\d{2,}[A-Z]?", c):
        return m.group(), ContentType.AMATEUR
    if m := _CATALOG_NUMBER.search(c):
        file_number = m.group()
        for key, value in _SUREN_PREFIXES.items():
            if key in file_number:
                file_number = value.replace(key, "") + file_number
                break
        return file_number, _type_of_catalog_id(file_number)
    if generic and (
        (m := re.search(r"[A-Z]+-[A-Z]\d+", c))
        or (m := re.search(r"\d{2,}[-_]\d{2,}", c))
        or (m := re.search(r"\d{3,}-[A-Z]{3,}", c))
    ):
        return m.group(), _type_of_catalog_id(m.group())
    if m := re.search(r"([^A-Z]|^)(N\d{4})(\D|$)", c):
        return m.group(2).lower(), ContentType.UNCENSORED
    if m := re.search(r"H_\d{3,}([A-Z]{2,})(\d{2,})", c):
        number = f"{m[1]}-{m[2]}"
        return number, _type_of_catalog_id(number)
    if generic and (m := re.findall(r"([A-Z]{3,}).*?(\d{2,})", c)):
        number = f"{m[0][0]}-{m[0][1]}"
        return number, _type_of_catalog_id(number)
    if generic and (m := re.findall(r"([A-Z]{2,}).*?(\d{3,})", c)):
        number = f"{m[0][0]}-{m[0][1]}"
        return number, _type_of_catalog_id(number)
    return None


def _fallback(stem: str, escape: list[str]) -> tuple[str, ContentType]:
    """路径仍未命中: FC2/HEYZO 关键字视为该族, 否则清理后的原文 + 欧美."""
    t = _prepare(stem, escape)
    if "FC2" in t.catalog:
        return t.catalog.replace("PPV", "").replace("_", "-").replace("--", "-"), ContentType.FC2
    if "HEYZO" in t.catalog:
        return t.catalog.replace("_", "-").replace("--", "-"), ContentType.UNCENSORED
    temp_name = re.sub(r"[【(（\[].+?[]）)】]", "", t.escaped).strip("@. ")
    temp_name = unicodedata.normalize("NFC", temp_name)
    with contextlib.suppress(Exception):
        temp_name = temp_name.encode("cp932").decode("shift_jis")
    result = temp_name.strip("-_. ")
    if result.startswith("FC-"):
        return result.replace("FC-", "FC2-"), ContentType.FC2
    return result, ContentType.WESTERN


def _type_of_catalog_id(number: str) -> ContentType:
    """PREFIX-NNN 族: 无码表 / 素人 / 国产 / 有码."""
    if re.match(r"n\d{4}", number) or re.search(r"[^.]+\.\d{2}\.\d{2}\.\d{2}", number):
        return ContentType.UNCENSORED
    upper = number.upper()
    if any(upper.startswith(prefix.upper()) for prefix in _UNCENSORED_PREFIXES):
        return ContentType.UNCENSORED
    if "SIRO" in upper or re.search(r"\d{3,}[A-Z]+-\d{2}", upper):
        return ContentType.AMATEUR
    if any(upper.startswith(key.upper()) for key in _SUREN_PREFIXES):
        return ContentType.AMATEUR
    if re.search(r"([^A-Z]|^)MD[A-Z-]*\d{4,}", upper) and "MDVR" not in upper:
        return ContentType.CHINESE
    if re.search(r"MKY-[A-Z]+-\d{3,}", upper):
        return ContentType.CHINESE
    if _CATALOG_NUMBER.search(upper):
        return ContentType.CENSORED
    return ContentType.WESTERN


def _prefix(number: str) -> str:
    upper = number.upper()
    if m := re.search(r"([A-Za-z0-9-.]{3,})[-_. ]\d{2}\.\d{2}\.\d{2}", number):
        return m[1].upper()
    for prefix in ("FC2", "MYWIFE", "KIN8", "S2M", "T28", "TH101", "XXX-AV"):
        if upper.startswith(prefix):
            return prefix
    if m := re.search(r"(MKY-[A-Z]+)-\d{3,}", upper):
        return m[1]
    if m := re.search(r"(H4610|C0930|H0930)", upper):
        return m[1]
    if m := re.search(r"(\d*[A-Za-z]+)\d*", number):
        return m[1].upper()
    return number.upper()


# ---------------------------------------------------------------------------
# Path keywords + file phase
# ---------------------------------------------------------------------------


def _dir_names(path: Path) -> tuple[str, ...]:
    return tuple(p for p in path.parent.parts if p not in _ROOT_PARTS)


def _classify_from_path(stem: str, dir_names: tuple[str, ...]) -> ContentType | None:
    """getchu 整段相等; 里番/裏番/欧美必须段首, 避免 这里番号 / 非欧美."""
    for name in (stem, *dir_names):
        if name.lower() == "getchu":
            return ContentType.HENTAI
        if name.startswith(("里番", "裏番")):
            return ContentType.HENTAI
        if name.startswith("欧美"):
            return ContentType.WESTERN
    return None


def _detect_cd(basename: str) -> int | None:
    if m := re.search(r"[-_.]CD(\d{1,2})", basename):
        return int(m[1])
    if m := re.search(r"[-_.]PART(\d{1,2})", basename):
        return int(m[1])
    if m := re.search(r"-([AB])(?:\.|$)", basename):
        return ord(m[1]) - ord("A") + 1
    if m := re.search(r"-([1-9])$", basename):
        return int(m[1])
    return None


def _detect_cd_from_parent(parent: str) -> int | None:
    m = _CD_DIR.fullmatch(parent.strip())
    if m is None:
        return None
    n = int(m[1])
    return n if n >= 1 else None


def _detect_subtitle(basename: str) -> bool:
    if re.search(r"-U?C(?![A-Z0-9])", basename):
        return True
    return bool(re.search(r"[字幕中文]", basename))


def _detect_mosaic(basename: str) -> Mosaic | None:
    if re.search(r"無碼|无码|UNCENSORED", basename):
        return Mosaic.UNCENSORED
    if re.search(r"-U(C)?(?![A-Z0-9])", basename):
        return Mosaic.UNCENSORED
    if re.search(r"破解", basename):
        return Mosaic.CRACKED
    if re.search(r"流出|LEAKED", basename):
        return Mosaic.LEAKED
    return None


def _detect_mosaic_from_dirs(dir_names: tuple[str, ...]) -> Mosaic | None:
    for name in reversed(dir_names):
        token = name.strip().strip(_DIR_BRACKET_STRIP).casefold()
        if token in _MOSAIC_DIR_TOKENS:
            return _MOSAIC_DIR_TOKENS[token]
    return None


def _normalize_markers(text: str) -> str:
    return re.sub(r"_|[^\x00-\x7F]", ".", text)


def _detect_definition(basename: str) -> str | None:
    normalized = _normalize_markers(basename)
    for value, pattern in _DEFINITION_MARKERS:
        if pattern.search(normalized):
            return value
    return None
