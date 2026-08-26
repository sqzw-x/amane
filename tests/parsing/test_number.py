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
