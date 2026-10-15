"""测试爬虫共用的解析工具: HTML 提取, 番号与名字归一."""

import pytest
from parsel import Selector

from amane.crawlers.parsing import (
    CSSSelector,
    NumberMatch,
    clean_string,
    extract_all_texts,
    extract_text,
    fold_name,
    is_same_name,
    is_same_number,
    leading_token,
    normalize_name,
    number_match,
    number_variants,
    parse_western_number,
    select_number_index,
)

SAMPLE_HTML = """
<html>
<body>
  <h1 class="title">  Hello World  </h1>
  <div class="info">
    <span class="label">Studio:</span>
    <span class="value"><a href="/s/1">Studio A</a></span>
  </div>
  <div class="tags">
    <a href="/t/1">Tag 1</a>
    <a href="/t/2">Tag 2</a>
    <a href="/t/3">Tag 3</a>
  </div>
  <div class="empty"></div>
</body>
</html>
"""


class TestCleanString:
    def test_strips_whitespace(self):
        assert clean_string("  hello  ") == "hello"

    def test_removes_newlines(self):
        assert clean_string("hello\nworld\r") == "helloworld"

    def test_replaces_nbsp(self):
        assert clean_string("hello&nbsp;world") == "hello world"

    def test_none_returns_empty(self):
        assert clean_string(None) == ""

    def test_empty_returns_empty(self):
        assert clean_string("") == ""


class TestExtractText:
    def test_xpath(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_text(sel, "//h1[@class='title']/text()")
        assert result == "Hello World"

    def test_css(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_text(sel, CSSSelector("h1.title::text"))
        assert result == "Hello World"

    def test_fallback_to_second_selector(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_text(sel, "//h1[@class='nonexistent']/text()", "//h1[@class='title']/text()")
        assert result == "Hello World"

    def test_no_match_returns_empty(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_text(sel, "//h1[@class='nonexistent']/text()")
        assert result == ""


class TestExtractAllTexts:
    def test_extracts_list(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_all_texts(sel, "//div[@class='tags']/a/text()")
        assert result == ["Tag 1", "Tag 2", "Tag 3"]

    def test_no_match_returns_empty_list(self):
        sel = Selector(text=SAMPLE_HTML)
        result = extract_all_texts(sel, "//div[@class='nothing']/a/text()")
        assert result == []


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # 片商名折叠大小写与非字母数字; alternate 是同番号的另一种年份写法
        ("Blacked.26.03.29", ("blacked", "2026-03-29", "Blacked.2026.03.29")),
        ("Blacked.2026.03.29", ("blacked", "2026-03-29", "Blacked.26.03.29")),
        ("blacked-26-03-29", ("blacked", "2026-03-29", "blacked-2026-03-29")),
        ("X-art.18.06.26", ("xart", "2018-06-26", "X-art.2018.06.26")),
        # 非日期号与非法日期不作日期号解析
        ("SSIS-497", None),
        ("300MIUM-1000", None),
        ("010115_001", None),
        ("Blacked.26.13.45", None),
        ("Blacked.26.02.30", None),
        ("", None),
    ],
)
def test_parse_western_number(number: str, expected: tuple[str, str, str] | None) -> None:
    result = parse_western_number(number)
    if expected is None:
        assert result is None
        return
    assert result is not None
    assert (result.studio, result.date, result.alternate) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # 大小写与短横线 / 空格无关
        ("ABF355", "ABF-355", True),
        ("Heyzo-3607", "HEYZO3607", True),
        # 前缀与相近番号不是同一部
        ("ABF-35", "ABF-355", False),
        ("SSIS-497", "SSIS-4970", False),
        ("FC2-2476386", "FC2-3476386", False),
        # 欧美日期号: 年份 2 位与 4 位同番号; 片商或发布日期不同则不是
        ("Blacked.26.03.29", "Blacked.2026.03.29", True),
        ("Blacked.26.03.29", "Blacked.2026.03.24", False),
        ("Blacked.26.03.29", "BlackedRaw.2026.03.29", False),
        # ``_`` 与 ``-`` 是两部片
        ("010115_001", "010115-001", False),
        ("", "ABF-355", False),
        # 数字段的宽松判定是 opt-in, 不进 is_same_number
        ("JAC-079", "390JAC-079", False),
    ],
)
def test_is_same_number(left: str, right: str, expected: bool) -> None:
    assert is_same_number(left, right) is expected


NUMBER_MATCH_CASES: list[tuple[str, str, NumberMatch]] = [
    # 折叠后相等
    ("ABF355", "ABF-355", NumberMatch.EXACT),
    ("Heyzo-3607", "HEYZO3607", NumberMatch.EXACT),
    # 欧美日期号: 年份 2 位与 4 位同番号
    ("Blacked.2026.03.29", "Blacked.26.03.29", NumberMatch.EXACT),
    # 只有一侧写了开头的数字段: 站点省略系列代码, 或入参本身没写
    ("JAC-079", "390JAC-079", NumberMatch.WITHOUT_LEADING_DIGITS),
    ("390JAC-079", "JAC-079", NumberMatch.WITHOUT_LEADING_DIGITS),
    ("300MIUM-1219", "MIUM-1219", NumberMatch.WITHOUT_LEADING_DIGITS),
    ("MIUM-1000", "200MIUM-1000", NumberMatch.WITHOUT_LEADING_DIGITS),
    ("107HYPN-037", "HYPN-037", NumberMatch.WITHOUT_LEADING_DIGITS),
    # 两侧都写了数字段却不同 = 不同系列号的同名番号
    ("200MIUM-1000", "300MIUM-1000", NumberMatch.NONE),
    # 相近番号与序号位数都不折叠
    ("ABF-35", "ABF-355", NumberMatch.NONE),
    ("SSIS-497", "SSIS-4970", NumberMatch.NONE),
    ("JAC-79", "390JAC-079", NumberMatch.NONE),
    ("JAC-079", "390JAC-078", NumberMatch.NONE),
    # 裸数字号与无字母可剥的号不退化成空, 因此不会互相命中
    ("010115-001", "001", NumberMatch.NONE),
    ("n1234", "1234", NumberMatch.NONE),
    ("010115_001", "010115-001", NumberMatch.NONE),
    # 任一侧为空
    ("", "390JAC-079", NumberMatch.NONE),
    ("390JAC-079", "", NumberMatch.NONE),
]


@pytest.mark.parametrize(("found", "query", "expected"), NUMBER_MATCH_CASES)
def test_number_match(found: str, query: str, expected: NumberMatch) -> None:
    assert number_match(found, query) is expected


SELECT_NUMBER_CASES: list[tuple[list[str], str, int | None]] = [
    # 精确命中优先于宽松命中, 即使它排在后面
    (["JAC-078", "JAC-079", "390JAC-079"], "JAC-079", 1),
    # 只剩站点省略数字段这一种可能时取它
    (["JAC-078", "JAC-079"], "390JAC-079", 1),
    (["HYPN-038", "HYPN-037"], "107HYPN-037", 1),
    # 宽松候选不唯一 = 站点上确有同名不同系列号的条目, 视为未命中
    (["200MIUM-1000", "300MIUM-1000"], "MIUM-1000", None),
    # 候选写了数字段且与入参不同, 不是同一部
    (["200MIUM-1000"], "300MIUM-1000", None),
    # 无命中 / 空候选
    (["ABF-355"], "390JAC-079", None),
    ([], "390JAC-079", None),
    ([""], "390JAC-079", None),
]


@pytest.mark.parametrize(("found", "query", "expected"), SELECT_NUMBER_CASES)
def test_select_number_index(found: list[str], query: str, expected: int | None) -> None:
    assert select_number_index(found, query) == expected


NUMBER_VARIANT_CASES: list[tuple[str, tuple[str, ...]]] = [
    # 数字段写在番号里: 补上站点可能省略的写法
    ("390JAC-079", ("390JAC-079", "JAC-079")),
    ("107HYPN-037", ("107HYPN-037", "HYPN-037")),
    ("300MIUM-1219", ("300MIUM-1219", "MIUM-1219")),
    # 素人系标签已知站内写法: 补出数字段
    ("MIUM-1219", ("MIUM-1219", "300MIUM-1219")),
    ("ARA-423", ("ARA-423", "261ARA-423")),
    # 只有原样: 标签须自番号开头起算, 裸数字号与无数字段的号不派生
    ("GARA-006", ("GARA-006",)),
    ("MIDV-123", ("MIDV-123",)),
    ("n1234", ("n1234",)),
    ("010115-001", ("010115-001",)),
    ("", ("",)),
]


@pytest.mark.parametrize(("number", "expected"), NUMBER_VARIANT_CASES)
def test_number_variants(number: str, expected: tuple[str, ...]) -> None:
    assert number_variants(number) == expected


@pytest.mark.parametrize(
    ("value", "normalized", "folded"),
    [
        ("  相川美羽  ", "相川美羽", "相川美羽"),
        ("Miu Aikawa", "Miu Aikawa", "miu aikawa"),
        # 全角字母与全角空格按 NFKC 收成半角
        ("Ｍｉｕ　Ａｉｋａｗａ", "Miu Aikawa", "miu aikawa"),
        ("", "", ""),
    ],
)
def test_name_keys(value: str, normalized: str, folded: str) -> None:
    """取值归一保留大小写, 比较键再折叠大小写."""
    assert normalize_name(value) == normalized
    assert fold_name(value) == folded


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("Miu Aikawa", "miu aikawa", True),
        ("Ｍｉｕ Ａｉｋａｗａ", "Miu Aikawa", True),
        # 注记括号由站点侧剥离, 基准工具按整名比较
        ("相川美羽", "相川美羽(tenshigao)", False),
        ("あい", "あいだゆあ", False),
        ("", "", False),
        ("相川美羽", "", False),
    ],
)
def test_is_same_name(left: str, right: str, expected: bool) -> None:
    assert is_same_name(left, right) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SSIS-497 與最棒女性", "SSIS-497"),
        ("  MD-0180-2 夜勤病栋-下", "MD-0180-2"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_leading_token(text: str, expected: str) -> None:
    assert leading_token(text) == expected
