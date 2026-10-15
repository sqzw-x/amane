"""爬虫共用的小工具: 从 parsel Selector 提取文本, 番号与名字归一, 以及页面内嵌数据."""

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from re import Pattern
from typing import TYPE_CHECKING, Any

from ..parsing.file_info import SUREN_LABELS
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


#: 番号开头的数字段 (素人系的系列代码, 如 ``390JAC-079`` 的 ``390``). 站点写不写它不一致:
#: javdb 与 javbus 恒不写, jav321 恒写出, mgstage 逐部不同. 后面不是字母时不剥 (裸数字号不参与).
_LEADING_DIGITS = re.compile(r"^\d+(?=[a-z])", re.IGNORECASE)


def _split_leading_digits(number: str) -> tuple[str, str]:
    """折叠后的番号 → (开头的数字段, 其余部分); 没有数字段时数字段为空串."""
    folded = fold_number(number)
    match = _LEADING_DIGITS.match(folded)
    return (match.group(), folded[match.end() :]) if match else ("", folded)


def number_variants(number: str) -> tuple[str, ...]:
    """该番号在站点上可能的检索词, 原样写法在前, 去重保序.

    数字段以外的写法差异由来源自行承担, 这里只补两件事: 站点省略数字段时给出省略写法, 素人系标签已知
    数字段时给出站内写法. 来源逐个尝试, 原样命中即不再试其余写法.
    """
    variants = [number]
    match = _LEADING_DIGITS.match(number)
    if match:
        variants.append(number[match.end() :])
    else:
        upper = number.upper()
        for label, digits in SUREN_LABELS.items():
            if upper.startswith(label):
                variants.append(f"{digits}{number}")
                break
    return tuple(dict.fromkeys(variants))


class NumberMatch(StrEnum):
    """站内番号与检索番号的同一性, 声明顺序即精确度."""

    NONE = "none"
    WITHOUT_LEADING_DIGITS = "without_leading_digits"
    EXACT = "exact"


def number_match(found: str, query: str) -> NumberMatch:
    """站内番号与检索番号是否同一部, 以及精确度; 任一侧为空为 ``NONE``.

    ``EXACT``: 折叠后相等, 或同为欧美日期号且片商与发布日期相同.
    ``WITHOUT_LEADING_DIGITS``: 只有一侧写了开头的数字段 (站点省略系列代码, 或入参本身没写), 其余部分
    仍须**全等** — 这样 ``ABF-35`` 不会命中 ``ABF-375``, 两侧都写了数字段却不同也判为不是同一部
    (``200MIUM-1000`` 不是 ``300MIUM-1000``).
    """
    if not found or not query:
        return NumberMatch.NONE
    if fold_number(found) == fold_number(query):
        return NumberMatch.EXACT
    western = parse_western_number(found)
    if western is not None and western == parse_western_number(query):
        return NumberMatch.EXACT
    found_digits, found_rest = _split_leading_digits(found)
    query_digits, query_rest = _split_leading_digits(query)
    if found_rest == query_rest and not (found_digits and query_digits):
        return NumberMatch.WITHOUT_LEADING_DIGITS
    return NumberMatch.NONE


def is_same_number(left: str, right: str) -> bool:
    """两个番号是否同指一部: 折叠后相等, 或同为欧美日期号且片商与发布日期相同; 任一侧为空为假."""
    return number_match(left, right) is NumberMatch.EXACT


def select_number_index(found: Sequence[str], query: str) -> int | None:
    """候选番号中与 ``query`` 同一部者的下标; 无命中返回 ``None``.

    精确优先; 退而只在「仅差数字段」的候选**唯一**时取它 — 出现两个及以上说明站点上确有同名不同系列号的
    条目, 猜一个会返回无关影片, 因此视为未命中. 调用方按下标回填自己的条目 (URL 等).
    """
    exact: int | None = None
    loose: list[int] = []
    for index, item in enumerate(found):
        match number_match(item, query):
            case NumberMatch.EXACT:
                if exact is None:
                    exact = index
            case NumberMatch.WITHOUT_LEADING_DIGITS:
                loose.append(index)
            case NumberMatch.NONE:
                pass
    if exact is not None:
        return exact
    return loose[0] if len(loose) == 1 else None


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
