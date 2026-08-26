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


def _classify_from_path(path_lower: str) -> ContentType | None:
    """按路径关键词分类; 未命中返回 None."""

    # 里番
    if any(kw in path_lower for kw in ("getchu", "里番", "裏番")):
        return ContentType.HENTAI

    # 欧美 (来自路径关键词)
    if "欧美" in path_lower and "东欧美" not in path_lower:
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


def _match_number(basename: str, escape_strings: list[str]) -> str | None:
    """命中已知番号模式则返回, 否则 None. 不含文件名最终回退."""
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

    # 回退模式
    if (
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

    # 通用: 3 个以上字母 + 2 个以上数字
    if m := re.findall(r"([A-Z]{3,}).*?(\d{2,})", filename):
        return f"{m[0][0]}-{m[0][1]}"
    if m := re.findall(r"([A-Z]{2,}).*?(\d{3,})", filename):
        return f"{m[0][0]}-{m[0][1]}"

    return None


def _extract_number(basename: str, escape_strings: list[str]) -> str:
    """
    从文件名 (不含扩展名) 中提取媒体番号.

    先按级联正则模式匹配 (见 _match_number); 未命中则回退到清理后的文件名, 不会返回 None.
    """
    matched = _match_number(basename, escape_strings)
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
    mosaic: str | None = None
    definition: str | None = None


def parse_file_info(filepath: str | Path, escape_strings: list[str] | None = None) -> FileInfo:
    """从完整文件路径解析番号、内容类型、分集、字幕、马赛克、清晰度."""
    filepath = str(filepath)
    stem = Path(filepath).stem
    content_type = _classify_from_path(filepath.lower())
    number = _extract_number(stem.strip(), escape_strings or [])
    if content_type is None:
        content_type = classify_number(number)
    basename = stem.upper()

    return FileInfo(
        number=number,
        content_type=content_type,
        prefix=get_prefix(number),
        cd=_detect_cd(basename),
        has_subtitle=_detect_subtitle(basename),
        mosaic=_detect_mosaic(basename),
        definition=_detect_definition(basename),
    )


def infer_content_type(number: str, file_path: str | None = None) -> ContentType:
    """推断 content_type: 有挂载文件则按路径, 否则按番号."""
    if file_path is not None:
        return parse_file_info(file_path).content_type
    return classify_number(number)


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
    """从文件名检测分辨率标记. 无命中返回 None; 命中多个时取最高."""
    for value, pattern in _DEFINITION_MARKERS:
        if pattern.search(basename):
            return value
    return None
