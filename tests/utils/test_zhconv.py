import pytest

from amane.utils.zhconv import _parse_overrides, convert, issimp


@pytest.mark.parametrize(
    "s,locale,expected",
    [
        # 繁体 → 简体
        ("頭髮", "zh-cn", "头发"),
        ("干擾", "zh-cn", "干扰"),
        ("資訊", "zh-cn", "资讯"),
        ("軟體", "zh-cn", "软件"),  # 地区词
        ("奴隷", "zh-cn", "奴隶"),  # 异体字
        ("「極上」", "zh-cn", "“极上”"),
        # 简体 → 繁体
        ("头发", "zh-tw", "頭髮"),
        ("软件", "zh-tw", "軟體"),
        ("台湾", "zh-tw", "臺灣"),
        # 项目修正词条 (zhconv_overrides.json)
        ("公里", "zh-tw", "公里"),
        ("干擾", "zh-tw", "干擾"),
        ("舞台", "zh-tw", "舞臺"),
        # 未命中的字符原样保留
        ("abc 123", "zh-cn", "abc 123"),
        ("", "zh-cn", ""),
        # 未支持的 locale 原样返回
        ("頭髮", "zh-hk", "頭髮"),
        ("頭髮", "ja", "頭髮"),
    ],
)
def test_convert(s, locale, expected):
    assert convert(s, locale) == expected


@pytest.mark.parametrize(
    "s,expected",
    [
        ("头发", True),
        ("头发是好的", True),
        ("頭髮", False),
        ("這次來拍", False),
        ("你好", None),  # 无变体专用字
        ("", None),
        ("头发頭髮", None),  # 两类各一, 无法判断
    ],
)
def test_issimp_full(s, expected):
    assert issimp(s, full=True) == expected


@pytest.mark.parametrize(
    "s,expected",
    [
        ("头发", True),
        ("頭髮", False),
        ("头發", True),  # 首个变体字是简体
        ("發头", False),
        ("你好", None),
    ],
)
def test_issimp_first_hit(s, expected):
    assert issimp(s) == expected


def test_parse_overrides():
    parsed = _parse_overrides('{"zh-tw": {"干扰": "干擾"}}'.encode())
    assert parsed == {"zh-tw": {"干扰": "干擾"}}


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"zh-hk": {}}',
        b'{"zh-tw": {"a": 1}}',
        b'{"zh-tw": []}',
    ],
)
def test_parse_overrides_rejects(raw):
    with pytest.raises(ValueError):
        _parse_overrides(raw)
