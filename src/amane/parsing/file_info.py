"""从完整文件路径解析番号、内容类型、分集、字幕、马赛克、清晰度."""

import contextlib
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ContentType(StrEnum):
    """媒体内容分类 - 决定爬虫路由."""

    CENSORED = "censored"
    UNCENSORED = "uncensored"
    CHINESE = "chinese"
    WESTERN = "western"
    FC2 = "fc2"
    AMATEUR = "amateur"
    HENTAI = "hentai"


class Mosaic(StrEnum):
    """文件相位马赛克标记. 未检出是空, 不是 censored."""

    UNCENSORED = "uncensored"
    CRACKED = "cracked"
    LEAKED = "leaked"


# --- 常量 ---

# 已知无码番号前缀
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

# 素人番号前缀 - 前缀到完整番号前缀的映射
_SUREN_PREFIXES: dict[str, str] = {
    "LUXU-": "259LUXU-",
    "ARA-": "200GANA-",
    "MIUM-": "300MIUM-",
    "NTK-": "300NTK-",
    "MAAN-": "300MAAN-",
}

# 欧美站点简称到全称的映射
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

# 需要从文件名中移除的分辨率/编码标记
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


# --- 公共 API ---


def is_uncensored(number: str) -> bool:
    """检查番号是否为无码内容."""
    if re.match(r"n\d{4}", number):
        return True
    if re.search(r"[^.]+\.\d{2}\.\d{2}\.\d{2}", number):
        return True
    upper = number.upper()
    return any(upper.startswith(prefix.upper()) for prefix in _UNCENSORED_PREFIXES)


def is_amateur(number: str) -> bool:
    """检查番号是否为素人内容."""
    upper = number.upper()
    if "SIRO" in upper:
        return True
    if re.search(r"\d{3,}[A-Z]+-\d{2}", upper):
        return True
    return any(upper.startswith(key.upper()) for key in _SUREN_PREFIXES)


def get_prefix(number: str) -> str:
    """
    提取番号中的字母前缀.

    示例:
        "MIDV-123" -> "MIDV"
        "FC2-1234567" -> "FC2"
        "vixen.23.04.15" -> "VIXEN"
    """
    upper = number.upper()

    # 欧美格式: name.YY.MM.DD
    if m := re.search(r"([A-Za-z0-9-.]{3,})[-_. ]\d{2}\.\d{2}\.\d{2}", number):
        return m[1].upper()

    # 特殊前缀
    for prefix in ("FC2", "MYWIFE", "KIN8", "S2M", "T28", "TH101", "XXX-AV"):
        if upper.startswith(prefix):
            return prefix

    # MKY-X 模式
    if m := re.search(r"(MKY-[A-Z]+)-\d{3,}", upper):
        return m[1]

    # H4610/C0930/H0930
    if m := re.search(r"(H4610|C0930|H0930)", upper):
        return m[1]

    # 通用: 数字前的字母部分
    if m := re.search(r"(\d*[A-Za-z]+)\d*", number):
        return m[1].upper()

    return number.upper()


def extract_number(text: str, escape_strings: list[str] | None = None) -> str | None:
    """从自由文本提取番号.

    只在命中已知模式时返回; 未命中返回 None, 不会把清理后的原文冒充番号.
    RSS 标题等非文件名场景必须走这里, 不要用 parse_file_info.
    """
    return _match_number(text, escape_strings or [])


_ROOT_PARTS = frozenset({"/", ".", ""})


def _dir_names(path: Path) -> tuple[str, ...]:
    """路径中的目录名 (不含文件名, 根到近)."""
    return tuple(p for p in path.parent.parts if p not in _ROOT_PARTS)


def _classify_from_path(stem: str, dir_names: tuple[str, ...]) -> ContentType | None:
    """按路径段关键词分类; 未命中返回 None.

    getchu 必须整段相等 (忽略大小写), 避免 forgetchu / getchu-docs 子串误伤.
    里番/裏番/欧美必须在段首, 避免 这里番号 / 非欧美.
    """
    for name in (stem, *dir_names):
        if name.lower() == "getchu":
            return ContentType.HENTAI
        if name.startswith(("里番", "裏番")):
            return ContentType.HENTAI
        if name.startswith("欧美"):
            return ContentType.WESTERN
    return None


def classify_number(number: str) -> ContentType:
    """根据番号分类内容类型."""
    upper = number.upper()

    # FC2
    if "FC2" in upper:
        return ContentType.FC2

    # 欧美格式: name.YY.MM.DD
    if re.search(r"[^.]+\.\d{2}\.\d{2}\.\d{2}", number):
        return ContentType.WESTERN

    # 国产 (纯番号模式, 无需路径)
    if re.search(r"([^A-Z]|^)MD[A-Z-]*\d{4,}", upper) and "MDVR" not in upper:
        return ContentType.CHINESE
    if re.search(r"MKY-[A-Z]+-\d{3,}", upper):
        return ContentType.CHINESE

    # 无码
    if is_uncensored(number):
        return ContentType.UNCENSORED

    # 素人
    if is_amateur(number):
        return ContentType.AMATEUR

    # 默认: 有码
    return ContentType.CENSORED


def _remove_escape_strings(filename: str, escape_strings: list[str]) -> str:
    """移除用户指定的字符串和编码/分辨率标记."""
    upper = filename.upper()

    for string in escape_strings:
        if string:
            upper = upper.replace(string.upper(), "")

    for marker in _ESCAPE_MARKERS:
        upper = re.sub(rf"[-_ .\[]{marker}[-_ .\]]", "-", upper)

    return upper.replace("--", "-").strip("-_ .")


@dataclass(frozen=True)
class _NumberText:
    file_name: str
    filename: str
    western_filename: str


def _prepare_number_text(basename: str, escape_strings: list[str]) -> _NumberText:
    """标准化待提取文本 (去标记 / CD / 日期 / FC2 变体)."""
    real_name = basename.strip() + "."
    file_name = _remove_escape_strings(real_name, escape_strings) + "."
    filename = (
        file_name.replace("-C.", ".")
        .replace(".PART", "-CD")
        .replace("-PART", "-CD")
        .replace(" EP.", ".EP")
        .replace("-CD-", "")
    )
    filename = re.sub(r"[-_ .]CD\d{1,2}", "", filename)
    filename = re.sub(r"[-_ .][A-Z0-9]\.$", "", filename)
    filename = filename.replace(" ", "-").strip("-_. ")
    western_filename = filename
    filename = re.sub(r"\d{4}[-_.]\d{1,2}[-_.]\d{1,2}", "", filename)
    filename = re.sub(r"[-\[]\d{2}[-_.]\d{2}[-_.]\d{2}]?", "", filename)
    filename = (
        filename.replace("FC2-PPV", "FC2-").replace("FC2PPV", "FC2-").replace("--", "-").replace("GACHIPPV", "GACHI")
    )
    return _NumberText(file_name=file_name, filename=filename, western_filename=western_filename)


def _match_number(basename: str, escape_strings: list[str], *, generic: bool = True) -> str | None:
    """命中已知番号模式则返回, 否则 None. 不含文件名最终回退.

    generic=False 时跳过过宽的回退/字母数字拼接 (给目录名用, 避免 Season02 / 2024-01 冒充番号).
    """
    prepared = _prepare_number_text(basename, escape_strings)
    file_name = prepared.file_name
    filename = prepared.filename
    western_filename = prepared.western_filename

    # MYWIFE No.XXXX
    if "MYWIFE" in filename and re.search(r"NO\.\d*", filename):
        temp_num = re.findall(r"NO\.(\d*)", filename)[0]
        return f"Mywife No.{temp_num}"

    # CW3D2D 格式
    if m := re.search(r"CW3D2D?BD-?\d{2,}", filename):
        return m.group()

    # MMR 模式
    if m := re.search(r"MMR-?[A-Z]{2,}-?\d+[A-Z]*", filename):
        return m.group().replace("MMR-", "MMR")

    # 国产 MD 模式
    if (m := re.search(r"([^A-Z]|^)(MD[A-Z-]*\d{4,}(-\d)?)", file_name)) and "MDVR" not in file_name:
        return m.group(2)

    # 欧美格式: name.YY.MM.DD
    if re.findall(r"([A-Z0-9_]{2,})[-.]2?0?(\d{2}[-.]\d{2}[-.]\d{2})", western_filename):
        result = re.findall(r"([A-Z0-9-]{2,})[-_.]2?0?(\d{2}[-.]\d{2}[-.]\d{2})", western_filename)
        if result:
            short_name = result[0][0].strip("-").lower()
            full_name: str = _WESTERN_NAMES.get(short_name) or short_name
            return (
                full_name.lower().replace("-", "").replace(".", "") + "." + result[0][1].replace("-", ".")
            ).capitalize()

    # XXX-AV 或 MKY-X
    if (m := re.search(r"XXX-AV-\d{4,}", filename)) or (m := re.search(r"MKY-[A-Z]+-\d{3,}", filename)):
        return m.group()

    # FC2: 只接受带数字的形态, 关键字本身不算命中
    if "FC2" in filename:
        filename_fc2 = filename.replace("PPV", "").replace("_", "-").replace("--", "-")
        if m := re.search(r"FC2-\d{5,}", filename_fc2):
            return m.group()
        if m := re.search(r"FC2\d{5,}", filename_fc2):
            return m.group().replace("FC2", "FC2-")

    # HEYZO: 同上, 必须带数字
    if "HEYZO" in filename:
        filename_h = filename.replace("_", "-").replace("--", "-")
        if m := re.search(r"HEYZO-\d{3,}", filename_h):
            return m.group()
        if m := re.search(r"HEYZO\d{3,}", filename_h):
            return m.group().replace("HEYZO", "HEYZO-")

    # H4610/C0930/H0930
    if m := re.search(r"(H4610|C0930|H0930)-[A-Z]+\d{4,}", filename):
        return m.group()

    # KIN8
    if m := re.search(r"KIN8(TENGOKU)?-?\d{3,}", filename):
        return m.group().replace("TENGOKU", "-").replace("--", "-")

    # S2M / MCB3D
    if (m := re.search(r"S2M[BD]*-\d{3,}", filename)) or (m := re.search(r"MCB3D[BD]*-\d{2,}", filename)):
        return m.group()

    # T28
    if m := re.search(r"T28-?\d{3,}", filename):
        return m.group().replace("T2800", "T28-")

    # TH101
    if m := re.search(r"TH101-\d{3,}-\d{5,}", filename):
        return m.group().lower()

    # DMM 格式: SSNI00644 -> SSNI-644
    if m := re.search(r"([A-Z]{2,})00(\d{3})", filename):
        return f"{m[1]}-{m[2]}"

    # 素人番号: 259LUXU-1456
    if m := re.search(r"\d{2,}[A-Z]{2,}-\d{2,}[A-Z]?", filename):
        return m.group()

    # 标准格式: XXXX-NNN
    if m := re.search(r"[A-Z]{2,}-\d{2,}[Z]?", filename):
        file_number = m.group()
        for key, value in _SUREN_PREFIXES.items():
            if key in file_number:
                file_number = value.replace(key, "") + file_number
                break
        return file_number

    if generic and (
        (m := re.search(r"[A-Z]+-[A-Z]\d+", filename))
        or (m := re.search(r"\d{2,}[-_]\d{2,}", filename))
        or (m := re.search(r"\d{3,}-[A-Z]{3,}", filename))
    ):
        return m.group()

    # n1111 模式
    if m := re.search(r"([^A-Z]|^)(N\d{4})(\D|$)", filename):
        return m.group(2).lower()

    # h_173mega05 模式
    if m := re.search(r"H_\d{3,}([A-Z]{2,})(\d{2,})", filename):
        return f"{m[1]}-{m[2]}"

    if generic and (m := re.findall(r"([A-Z]{3,}).*?(\d{2,})", filename)):
        return f"{m[0][0]}-{m[0][1]}"
    if generic and (m := re.findall(r"([A-Z]{2,}).*?(\d{3,})", filename)):
        return f"{m[0][0]}-{m[0][1]}"

    return None


def _extract_number(basename: str, escape_strings: list[str], dir_names: tuple[str, ...] = ()) -> str:
    """从文件名提取番号; 未命中已知模式时再对父目录由近到远做同样匹配.

    目录只用已知番号模式 (不含过宽的字母数字拼接), 不会把清理后的目录名冒充番号.
    文件名仍未命中时回退到清理后的文件名, 不会返回 None.
    """
    matched = _match_number(basename, escape_strings)
    if matched is not None:
        return matched
    for name in reversed(dir_names):
        matched = _match_number(name, escape_strings, generic=False)
        if matched is not None:
            return matched

    prepared = _prepare_number_text(basename, escape_strings)
    filename = prepared.filename
    file_name = prepared.file_name

    # 文件名场景: FC2/HEYZO 关键字即视为番号族, 即使缺数字
    if "FC2" in filename:
        return filename.replace("PPV", "").replace("_", "-").replace("--", "-")
    if "HEYZO" in filename:
        return filename.replace("_", "-").replace("--", "-")

    # 最终回退: 清理后原样使用
    temp_name = re.sub(r"[【(（\[].+?[]）)】]", "", file_name).strip("@. ")
    temp_name = unicodedata.normalize("NFC", temp_name)
    # mojibake 修复: 日文文件名常被误用 cp932 编码再按 shift_jis 解出乱码,
    # 此处反向还原. 失败 (非该情形) 则保持原样, 故 suppress.
    with contextlib.suppress(Exception):
        temp_name = temp_name.encode("cp932").decode("shift_jis")

    result = temp_name.strip("-_. ")
    if result.startswith("FC-"):
        result = result.replace("FC-", "FC2-")
    return result


@dataclass(frozen=True)
class FileInfo:
    number: str
    content_type: ContentType
    prefix: str
    cd: int | None = None
    has_subtitle: bool = False
    mosaic: Mosaic | None = None
    definition: str | None = None


def parse_file_info(filepath: str | Path, escape_strings: list[str] | None = None) -> FileInfo:
    """从完整文件路径解析番号、内容类型、分集、字幕、马赛克、清晰度.

    文件名优先. 番号未命中已知模式时可从父目录回退; 马赛克可从目录名整段补;
    分集还可认直接父目录 CD/PART; 字幕与清晰度只看文件名.
    """
    path = Path(filepath)
    stem = path.stem
    dirs = _dir_names(path)
    content_type = _classify_from_path(stem, dirs)
    number = _extract_number(stem.strip(), escape_strings or [], dirs)
    if content_type is None:
        content_type = classify_number(number)
    basename = stem.upper()
    cd = _detect_cd(basename)
    if cd is None and dirs:
        cd = _detect_cd_from_parent(dirs[-1])
    mosaic = _detect_mosaic(basename)
    if mosaic is None:
        mosaic = _detect_mosaic_from_dirs(dirs)

    return FileInfo(
        number=number,
        content_type=content_type,
        prefix=get_prefix(number),
        cd=cd,
        has_subtitle=_detect_subtitle(basename),
        mosaic=mosaic,
        definition=_detect_definition(basename),
    )


def infer_content_type(number: str, file_path: str | None = None) -> ContentType:
    """推断 content_type: 有挂载文件则按路径, 否则按番号."""
    if file_path is not None:
        return parse_file_info(file_path).content_type
    return classify_number(number)


_CD_DIR = re.compile(r"^(?:CD|PART)(\d{1,2})$", re.IGNORECASE)


def detect_cd(filename: str | Path) -> int | None:
    """从文件名 (不含目录) 检测分集编号. 字幕配对只走这一层, 不看父目录."""
    return _detect_cd(Path(filename).stem.upper())


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


def _detect_cd_from_parent(parent: str) -> int | None:
    """直接父目录整段为 CDn / PARTn 时视为分集. 不含 -A / 裸数字, 也不看更远的祖先."""
    m = _CD_DIR.fullmatch(parent.strip())
    if m is None:
        return None
    n = int(m[1])
    return n if n >= 1 else None


def _detect_subtitle(basename: str) -> bool:
    """通过文件名标记检测是否包含字幕."""
    # -C / -UC 后不能紧跟字母或数字 (否则是 -CD1 分集、-CS 之类), 但可跟 -4K / -CD1 等标记段.
    if re.search(r"-U?C(?![A-Z0-9])", basename):
        return True
    return bool(re.search(r"[字幕中文]", basename))


def _detect_mosaic(basename: str) -> Mosaic | None:
    """从文件名检测马赛克/审查类型. 同名多标记时无码优先, 其次破解, 再流出."""
    if re.search(r"無碼|无码|UNCENSORED", basename):
        return Mosaic.UNCENSORED
    # -U / -UC 后不能紧跟字母或数字 (否则是 -UNKNOWN、-UC1), 但可跟 -4K / -CD1 等标记段.
    if re.search(r"-U(C)?(?![A-Z0-9])", basename):
        return Mosaic.UNCENSORED
    if re.search(r"破解", basename):
        return Mosaic.CRACKED
    if re.search(r"流出|LEAKED", basename):
        return Mosaic.LEAKED
    return None


# 目录名整段 (去括号后忽略大小写) 才计入; 值必须是 Mosaic (不含 censored: 那是无标记兜底).
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
_DIR_BRACKET_STRIP = "[]【】()（）"
# 路径模板映射校验 / schema 与 Mosaic 枚举同源, 不从词表 values 再推导.
MOSAIC_VALUES: tuple[Mosaic, ...] = tuple(Mosaic)


def _detect_mosaic_from_dirs(dir_names: tuple[str, ...]) -> Mosaic | None:
    """文件名无标记时, 由近到远认目录名整段. 子串 (uncensored-guide) 不算."""
    for name in reversed(dir_names):
        token = name.strip().strip(_DIR_BRACKET_STRIP).casefold()
        if token in _MOSAIC_DIR_TOKENS:
            return _MOSAIC_DIR_TOKENS[token]
    return None


def _normalize_markers(text: str) -> str:
    """把下划线与非 ASCII 字符 (含 CJK) 归一为分隔符, 使 \\b 词边界对汉字/下划线邻接也生效."""
    return re.sub(r"_|[^\x00-\x7F]", ".", text)


# 分辨率标记: 元组顺序即优先级 (高 → 低), 同时命中多个时取靠前者; 2160p 归一化为 4K.
# 匹配作用于 _normalize_markers 归一化后的 basename:
# - 数字标记 (8K/4K/NNNp) 允许紧跟帧率数字 (如 1080p60), K 与 p 后不得再接字母 (4KS/HDTV 不命中);
# - 字母标记 (HD/SD) 不得是番号前缀: HD-123 / HD_123 (下划线归一成点后同形) 不命中.
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
# 与路径模板映射校验 / schema 同源; 去重后保持检测优先级顺序 (2160p 已归一进 4K).
DEFINITION_VALUES: tuple[str, ...] = tuple(dict.fromkeys(value for value, _ in _DEFINITION_MARKERS))


def _detect_definition(basename: str) -> str | None:
    """从文件名检测分辨率标记. 无命中返回 None; 命中多个时取最高."""
    normalized = _normalize_markers(basename)
    for value, pattern in _DEFINITION_MARKERS:
        if pattern.search(normalized):
            return value
    return None
