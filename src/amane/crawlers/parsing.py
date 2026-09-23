"""爬虫共用的小工具: 从 parsel Selector 提取文本, 以及番号归一."""

import re
from re import Pattern
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from parsel import Selector


class CSSSelector(str):
    __slots__ = ()


type SelectorType = CSSSelector | Pattern | str


def clean_string(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().replace("\n", "").replace("\r", "").replace("&nbsp;", " ")


def extract_text(html: Selector, *selectors: SelectorType) -> str:
    for s in selectors:
        try:
            if isinstance(s, re.Pattern):
                result = html.re(s)
                result = result[0] if result else ""
            elif isinstance(s, CSSSelector):
                result = html.css(s).get()
            else:
                result = html.xpath(s).get()
            if result:
                return clean_string(result)
        except AttributeError, TypeError, IndexError:
            continue
    return ""


def extract_all_texts(html: Selector, *selectors: SelectorType) -> list[str]:
    for s in selectors:
        try:
            if isinstance(s, re.Pattern):
                results = html.re(s)
            elif isinstance(s, CSSSelector):
                results = html.css(s).getall()
            else:
                results = html.xpath(s).getall()
            if results:
                return [clean_string(r) for r in results if clean_string(r)]
        except AttributeError, TypeError, IndexError:
            continue
    return []


def fold_number(number: str) -> str:
    """大小写、短横线、空格视为同一番号, 供来源比对检索结果.

    不允许把 ``_`` 当作 ``-``: 010115_001 与 010115-001 是两部片.
    """
    return number.casefold().replace("-", "").replace(" ", "")
