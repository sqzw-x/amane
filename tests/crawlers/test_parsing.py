"""测试独立的 HTML 解析工具函数"""

import pytest
from parsel import Selector

from amane.crawlers.parsing import (
    CSSSelector,
    clean_string,
    extract_all_texts,
    extract_text,
    is_same_number,
    parse_western_number,
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
    ],
)
def test_is_same_number(left: str, right: str, expected: bool) -> None:
    assert is_same_number(left, right) is expected
