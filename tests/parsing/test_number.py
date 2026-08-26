"""测试 amane.parsing - 番号解析与分类"""

import pytest

from amane.parsing import (
    ContentType,
    classify_number,
    extract_number,
    get_prefix,
    infer_content_type,
    is_amateur,
    is_uncensored,
    parse_file_info,
)

# --- is_uncensored 测试 ---

UNCENSORED_NUMBERS = ["HEYZO-1234", "S2M-001", "CWP-123", "n1234", "BT-123", "SKY-001", "XXX-AV-12345", "MKBD-S120"]
CENSORED_NUMBERS = ["MIDV-123", "SSIS-456", "ABP-789", "IPX-001", "FC2-1234567"]


@pytest.mark.parametrize("number", UNCENSORED_NUMBERS)
def test_is_uncensored_true(number: str):
    assert is_uncensored(number) is True


@pytest.mark.parametrize("number", CENSORED_NUMBERS)
def test_is_uncensored_false(number: str):
    assert is_uncensored(number) is False


def test_western_format_is_uncensored():
    assert is_uncensored("vixen.23.04.15") is True


# --- is_amateur 测试 ---


@pytest.mark.parametrize("number", ["SIRO-1234", "LUXU-1234", "259LUXU-1456"])
def test_is_amateur_true(number: str):
    assert is_amateur(number) is True


def test_is_amateur_false():
    assert is_amateur("MIDV-123") is False


# --- get_prefix 测试 ---

PREFIX_CASES = [
    ("MIDV-123", "MIDV"),
    ("FC2-1234567", "FC2"),
    ("HEYZO-1234", "HEYZO"),
    ("vixen.23.04.15", "VIXEN"),
    ("MKY-HS-001", "MKY-HS"),
    ("H4610-ki123456", "H4610"),
]


@pytest.mark.parametrize("number,expected", PREFIX_CASES)
def test_get_prefix(number: str, expected: str):
    assert get_prefix(number) == expected


# --- parse_file_info (番号与内容类型) ---

PARSE_CASES = [
    # (文件路径, 期望番号, 期望类型)
    # 标准有码
    ("/media/MIDV-123.mp4", "MIDV-123", ContentType.CENSORED),
    ("/media/SSIS-456.mp4", "SSIS-456", ContentType.CENSORED),
    # FC2
    ("/media/FC2-PPV-1234567.mp4", "FC2-1234567", ContentType.FC2),
    ("/media/FC2PPV1234567.mp4", "FC2-1234567", ContentType.FC2),
    # 无码
    ("/media/HEYZO-1234.mp4", "HEYZO-1234", ContentType.UNCENSORED),
    ("/media/H4610-ki221218.mp4", "H4610-KI221218", ContentType.UNCENSORED),
    ("/media/S2MBD-006.mp4", "S2MBD-006", ContentType.UNCENSORED),
    # 国产
    ("/media/MD0165-1.mp4", "MD0165-1", ContentType.CHINESE),
    # 素人
    ("/media/259LUXU-1456.mp4", "259LUXU-1456", ContentType.AMATEUR),
    ("/media/SIRO-4567.mp4", "SIRO-4567", ContentType.AMATEUR),
    # DMM 格式: SSNI00321 → SSNI-321
    ("/media/SSNI00321.mp4", "SSNI-321", ContentType.CENSORED),
    # Mywife
    ("/media/Mywife No.1234.mp4", "Mywife No.1234", ContentType.CENSORED),
    # 基于路径的分类
    ("/media/里番/something-123.mp4", None, ContentType.HENTAI),
    ("/media/欧美/MIDV-123.mp4", None, ContentType.WESTERN),
]


@pytest.mark.parametrize("filepath,expected_number,expected_type", PARSE_CASES)
def test_parse_file_info_number(filepath: str, expected_number: str | None, expected_type: ContentType):
    result = parse_file_info(filepath)
    assert result.content_type == expected_type
    if expected_number is not None:
        assert result.number == expected_number


# --- parse_file_info: 特殊行为 ---


def test_multipart_cd_stripped():
    assert parse_file_info("/media/MIDV-123-CD1.mp4").number == "MIDV-123"
    assert parse_file_info("/media/SSIS-456 CD2.mp4").number == "SSIS-456"


def test_resolution_markers_stripped():
    assert parse_file_info("/media/[HD]SSIS-456.mp4").number == "SSIS-456"
    assert parse_file_info("/media/MIDV-123-4K.mp4", escape_strings=["something"]).number == "MIDV-123"


def test_western_date_in_number():
    result = parse_file_info("/media/Vixen.23.04.15.mp4")
    assert result.content_type == ContentType.WESTERN
    assert "23.04.15" in result.number


def test_kin8_number():
    result = parse_file_info("/media/KIN8TENGOKU-1234.mp4")
    assert "KIN8" in result.number and "1234" in result.number


# --- classify_number / infer_content_type (番号级分类) ---

CLASSIFY_NUMBER_CASES = [
    # (番号, 期望类型)
    ("MIDV-123", ContentType.CENSORED),
    ("SSIS-456", ContentType.CENSORED),
    ("FC2-PPV-1234567", ContentType.FC2),
    ("vixen.23.04.15", ContentType.WESTERN),
    ("HEYZO-1234", ContentType.UNCENSORED),
    ("SIRO-4567", ContentType.AMATEUR),
    # 国产纯番号模式 (无需路径)
    ("MD-0123", ContentType.CHINESE),
    ("MD0165-1", ContentType.CHINESE),
    ("MKY-NS-012", ContentType.CHINESE),
    # MDVR 排除
    ("MDVR-0123", ContentType.CENSORED),
]


@pytest.mark.parametrize("number,expected", CLASSIFY_NUMBER_CASES)
def test_classify_number(number: str, expected: ContentType):
    assert classify_number(number) == expected


INFER_CONTENT_TYPE_CASES = [
    # (番号, 文件路径或 None, 期望类型)
    ("MIDV-123", "/media/欧美/MIDV-123.mp4", ContentType.WESTERN),  # 路径关键词优先
    ("MIDV-123", "/media/MIDV-123.mp4", ContentType.CENSORED),  # 无路径关键词 → 番号
    ("MIDV-123", None, ContentType.CENSORED),  # 无文件 → 番号
    ("MD-0123", None, ContentType.CHINESE),
]


@pytest.mark.parametrize("number,file_path,expected", INFER_CONTENT_TYPE_CASES)
def test_infer_content_type(number: str, file_path: str | None, expected: ContentType):
    assert infer_content_type(number, file_path) == expected


# --- extract_number (自由文本, 无文件名回退) ---

EXTRACT_NUMBER_CASES = [
    ("[4K] MIDV-123 タイトル", "MIDV-123"),
    ("SSIS-456 FHD", "SSIS-456"),
    ("FC2-PPV-1234567 新作", "FC2-1234567"),
    ("HEYZO-1234", "HEYZO-1234"),
    ("259LUXU-1456", "259LUXU-1456"),
    ("今週の新作をお届けします", None),
    ("Weekly Update", None),
    ("", None),
    ("FC2 配信開始", None),
]


@pytest.mark.parametrize("text,expected", EXTRACT_NUMBER_CASES)
def test_extract_number(text: str, expected: str | None):
    assert extract_number(text) == expected


def test_extract_number_does_not_use_filename_fallback():
    """无番号的普通标题不得变成假番号."""
    assert extract_number("just a movie title") is None
    assert parse_file_info("just a movie title.mp4").number  # 文件名路径仍有回退
