"""normalize_long_text 表测试."""

from __future__ import annotations

import pytest

from amane.utils.text import normalize_long_text

CASES: list[tuple[str | None, str | None]] = [
    (None, None),
    ("", None),
    ("   ", None),
    ("\xa0", None),
    ("<br>", None),
    ("<p></p>", None),
    # 换行: 标签与空行
    ("前戏<br>高潮", "前戏\n高潮"),
    ("前戏<br/>高潮", "前戏\n高潮"),
    ("前戏<br />高潮", "前戏\n高潮"),
    ("前戏<BR>高潮", "前戏\n高潮"),
    ("一<br><br>二", "一\n\n二"),
    ("第一行\r\n第二行", "第一行\n第二行"),
    ("第一行\r第二行", "第一行\n第二行"),
    ("<div><p>甲<br />乙</p></div>", "甲\n乙"),
    ("a\n\n\n\nb", "a\n\nb"),
    ("  首  \n  尾  ", "首\n尾"),
    # 实体: 解码一次, 不产生二次转义
    ("S&amp;M プレイ", "S&M プレイ"),
    ("&#038;演出", "&演出"),
    ("AT&amp;amp;T", "AT&amp;T"),
    # 异体空白收成普通空格
    ("a&nbsp;b", "a b"),
    ("a\u2003b", "a b"),
    ("a\u3000b", "a b"),
    # 非法 XML 字符剥离
    ("a\x0bb\x0cc", "abc"),
    ("a\x00b", "ab"),
    # 不是标签的尖括号保持原样
    ("5 < 6 > 4", "5 < 6 > 4"),
    ("A &lt; B", "A < B"),
    # 实体编码的标签按标签处理 (幂等优先)
    ("x &lt;br&gt; y", "x\ny"),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize_long_text(raw: str | None, expected: str | None) -> None:
    assert normalize_long_text(raw) == expected


_DOUBLE_ENCODED = {"AT&amp;amp;T"}


@pytest.mark.parametrize(("raw", "_expected"), [c for c in CASES if c[0] not in _DOUBLE_ENCODED])
def test_normalize_is_idempotent(raw: str | None, _expected: str | None) -> None:
    """聚合出口与落库钩子都会调用, 二次调用必须不再变化."""
    once = normalize_long_text(raw)
    assert normalize_long_text(once) == once


def test_double_encoded_entities_decode_per_pass() -> None:
    """双重编码是唯一的非幂等输入: 上游二次转义的实体每次调用再解一层."""
    assert normalize_long_text("AT&amp;amp;T") == "AT&amp;T"
    assert normalize_long_text(normalize_long_text("AT&amp;amp;T")) == "AT&T"
