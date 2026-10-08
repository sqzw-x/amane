"""爬虫共用的小工具: 从 parsel Selector 提取文本, 番号与名字归一, 以及页面内嵌数据."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from re import Pattern
from typing import TYPE_CHECKING, Any

from ..utils.dates import normalize_calendar_date

if TYPE_CHECKING:
    from parsel import Selector

# Next.js Pages Router 把服务端数据内嵌在 ``__NEXT_DATA__`` 脚本里, 与 DOM 结构解耦.
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


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


def next_data_props(html: str) -> dict[str, Any] | None:
    """取内嵌服务端数据的 ``props.pageProps``; 无脚本 / JSON 不合法 / 结构不符时返回 None."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    props = data.get("props") if isinstance(data, dict) else None
    page = props.get("pageProps") if isinstance(props, dict) else None
    return page if isinstance(page, dict) else None


def fold_number(number: str) -> str:
    """大小写、短横线、空格视为同一番号, 供来源比对检索结果.

    不允许把 ``_`` 当作 ``-``: 010115_001 与 010115-001 是两部片.
    """
    return number.casefold().replace("-", "").replace(" ", "")


# 欧美日期号: 片商名可含 ``-``, 因此片商段用非贪婪匹配回溯.
_WESTERN_NUMBER = re.compile(
    r"^(?P<studio>.+?)(?P<sep0>[._-])(?P<year>\d{4}|\d{2})(?P<sep1>[._-])(?P<month>\d{2})(?P<sep2>[._-])(?P<day>\d{2})$"
)


@dataclass(frozen=True)
class WesternNumber:
    """欧美日期号 ``Studio.YY.MM.DD`` 的解析结果.

    ``alternate`` 是同一番号的另一种年份写法, 不参与相等判定.
    """

    studio: str
    date: str
    alternate: str = field(compare=False)


def fold_studio(name: str) -> str:
    """片商名折叠: 忽略大小写与非字母数字, 供番号与站内片商名比较."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", name).casefold() if ch.isalnum())


def parse_western_number(number: str) -> WesternNumber | None:
    """``Studio.YY.MM.DD`` → 折叠片商、``YYYY-MM-DD`` 与另一种年份写法; 其它形态与非法日期返回 None.

    2 位年份一律按 20xx 解读 (欧美日期号没有 19xx 的写法).
    """
    match = _WESTERN_NUMBER.match(number.strip())
    if match is None:
        return None
    year = match["year"]
    full_year = year if len(year) == 4 else f"20{year}"
    date = f"{full_year}-{match['month']}-{match['day']}"
    if normalize_calendar_date(date) != date:
        return None
    alternate_year = year[2:] if len(year) == 4 else f"20{year}"
    alternate = "".join(
        (match["studio"], match["sep0"], alternate_year, match["sep1"], match["month"], match["sep2"], match["day"])
    )
    return WesternNumber(studio=fold_studio(match["studio"]), date=date, alternate=alternate)


def is_same_number(left: str, right: str) -> bool:
    """两个番号是否同指一部: 折叠后相等, 或同为欧美日期号且片商与发布日期相同; 任一侧为空为假."""
    if not left or not right:
        return False
    if fold_number(left) == fold_number(right):
        return True
    western = parse_western_number(left)
    return western is not None and western == parse_western_number(right)


def normalize_name(value: str) -> str:
    """名字取值归一: NFKC 折叠 + 去首尾空白, 保留大小写. 供落库与展示取值."""
    return unicodedata.normalize("NFKC", value).strip()


def fold_name(value: str) -> str:
    """名字比较键: ``normalize_name`` 之后大小写无关; 空名归一为空串."""
    return normalize_name(value).casefold()


def is_same_name(left: str, right: str) -> bool:
    """两个名字是否同一人 (含别名写法): 折叠后相等; 任一侧为空为假."""
    return bool(left) and bool(right) and fold_name(left) == fold_name(right)


def leading_token(text: str) -> str:
    """取空白分隔的首个词 (条目标题常以番号开头); 无词返回空串."""
    parts = text.split()
    return parts[0] if parts else ""
