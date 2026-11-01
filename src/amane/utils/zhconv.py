"""简繁与地区词转换, 内置自 zhconv 1.4.3 的核心裁剪.

上游代码 https://github.com/gumblex/zhconv (MIT), 词表来自 MediaWiki 的 ZhConversion 与 OpenCC 词汇表
(GPLv2+), 项目按 GPLv3 分发. 只保留 ``convert`` 与 ``issimp`` 以及 zh-cn / zh-tw 两种变体:
最大正向匹配, 不做分词, 不解析 MediaWiki 的转换语法. 不追求通用转换质量, 只服务元数据字形归一.

词表数据是独立的 ``zhcdict.json``, 项目修正词条在 ``zhconv_overrides.json``; 两者都不在静态导入图内,
打包 (wheel / PyInstaller) 须显式收集.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import TypedDict, cast

LOCALES = ("zh-cn", "zh-tw")
"""支持的地区变体. 其余 locale 原样返回."""


class _Dictionary(TypedDict):
    SIMPONLY: list[str]
    """简体专用字 (繁转简时必变)."""
    TRADONLY: list[str]
    """繁体专用字 (简转繁时必变)."""
    zh2CN: dict[str, str]
    zh2Hans: dict[str, str]
    zh2Hant: dict[str, str]
    zh2TW: dict[str, str]


@lru_cache(maxsize=1)
def _dictionary() -> _Dictionary:
    return cast("_Dictionary", json.loads(files("amane.utils").joinpath("zhcdict.json").read_bytes()))


def _parse_overrides(raw: bytes) -> dict[str, dict[str, str]]:
    """校验 ``zhconv_overrides.json``: 顶层是 locale → 词条映射, 所有值都是字符串."""
    data: object = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("zhconv_overrides.json 顶层必须是对象")
    overrides: dict[str, dict[str, str]] = {}
    for locale, table in data.items():
        if locale not in LOCALES:
            raise ValueError(f"zhconv_overrides.json 含未知 locale: {locale!r}")
        if not isinstance(table, dict) or not all(
            isinstance(word, str) and isinstance(target, str) for word, target in table.items()
        ):
            raise ValueError(f"zhconv_overrides.json 的 {locale} 必须是字符串映射")
        overrides[locale] = dict(table)
    return overrides


@lru_cache(maxsize=1)
def _overrides() -> dict[str, dict[str, str]]:
    return _parse_overrides(files("amane.utils").joinpath("zhconv_overrides.json").read_bytes())


@lru_cache(maxsize=2)
def _table(locale: str) -> dict[str, str]:
    """字形表叠加地区词表, 再叠加项目修正词条; 都是「最长匹配优先」的候选.

    修正词条在 ``zhconv_overrides.json``, 针对 MediaWiki 表的缺词与同形字: 「干扰」缺词会被逐字
    译成「幹擾」, 「公里」「干擾」本身已是合法繁体, 二次转换会把它们改坏. 新增条目须对两种目标变体都无回归.
    """
    data = _dictionary()
    table = dict(data["zh2Hans"]) if locale == "zh-cn" else dict(data["zh2Hant"])
    table.update(data["zh2CN"] if locale == "zh-cn" else data["zh2TW"])
    table.update(_overrides().get(locale, {}))
    return table


@lru_cache(maxsize=2)
def _prefixes(locale: str) -> frozenset[str]:
    """词条的所有前缀; 最大正向匹配遇到前缀不在其中即可停止回溯."""
    return frozenset(word[:i] for word in _table(locale) for i in range(1, len(word) + 1))


@lru_cache(maxsize=1)
def _variant_chars() -> tuple[frozenset[str], frozenset[str]]:
    data = _dictionary()
    return frozenset(data["SIMPONLY"]), frozenset(data["TRADONLY"])


def issimp(s: str, *, full: bool = False) -> bool | None:
    """判断文本是简体 (``True``) 还是繁体 (``False``); 无法判断返回 ``None``.

    ``full=False`` 遇到第一个变体专用字即返回, 用于快速判定; ``full=True`` 比较两类字的数量.
    只有目标变体与文本变体一致时才应跳过转换, 否则已是正确繁体的文本会被二次改写.
    """
    simp, trad = _variant_chars()
    if not full:
        for ch in s:
            if ch in simp:
                return True
            if ch in trad:
                return False
        return None
    simp_count = trad_count = 0
    for ch in s:
        if ch in simp:
            simp_count += 1
        elif ch in trad:
            trad_count += 1
    if simp_count > trad_count:
        return True
    if simp_count < trad_count:
        return False
    return None


def convert(s: str, locale: str) -> str:
    """按 ``locale`` 转换文本; 未知 locale 原样返回.

    最大正向匹配: 优先匹配词表中最长的词条, 未命中则逐字保留.
    """
    if locale not in LOCALES:
        return s
    table = _table(locale)
    prefixes = _prefixes(locale)
    out: list[str] = []
    pos = 0
    length = len(s)
    while pos < length:
        i = pos
        frag = s[pos]
        matched: str | None = None
        end = pos
        while i < length and frag in prefixes:
            if frag in table:
                matched = table[frag]
                end = i
            i += 1
            frag = s[pos : i + 1]
        if matched is None:
            out.append(s[pos])
            pos += 1
        else:
            out.append(matched)
            pos = end + 1
    return "".join(out)
