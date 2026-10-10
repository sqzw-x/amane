"""标题清洗: 剔除标题中出现的演员展示名.

调用时机是聚合完成、翻译之前 (#293 要求名字不能进入翻译), 因此本模块只做纯文本变换, 不查库:
参与匹配的名字由调用方给出, 别名不在其中.

匹配口径取保守侧: 只认全名子串, 不认姓氏与别名 — 单字姓氏在标题里出现的概率远高于它指该演员的概率.
CJK 没有可用的词边界, 子串匹配即会命中更长词里的同名片段; 纯 ASCII 名字另加字母数字边界,
避免 ``AIKA`` 命中 ``AIKATSU``. 短于 ``_MIN_NAME_LENGTH`` 的名字一律不参与.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 括号内只剩空白与分隔符时整对删除: 剔名后残留的注记壳.
_EMPTY_BRACKETS = re.compile(r"[\[(（【〔［〈《]\s*[-–—|/・,，、:：]*\s*[\])）】〕］〉》]")
_SEPARATORS = r"\-–—|/・,，、:："
_EDGE_SEPARATORS = re.compile(rf"^[\s{_SEPARATORS}]+|[\s{_SEPARATORS}]+$")
_WHITESPACE_RUN = re.compile(r"\s+")
_ASCII_NAME = re.compile(r"[A-Za-z0-9]+")

_MIN_NAME_LENGTH = 2
"""短于此长度的名字不参与匹配."""


def strip_actor_names(title: str | None, names: Iterable[str]) -> str | None:
    """剔除 ``title`` 中出现的 ``names``.

    无命中时逐字返回原标题, 不做空白归一 — 未启用该步骤的标题必须保持不变. 剔除后清理残留分隔符与
    空括号; 结果为空时返回原标题 (标题不允许被清空).
    """
    if not title:
        return title
    candidates = {name.strip() for name in names if len(name.strip()) >= _MIN_NAME_LENGTH}
    stripped = title
    # 长名优先: 短名是长名前缀时先删短名会把长名切出残留.
    for name in sorted(candidates, key=len, reverse=True):
        pattern = (
            rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])" if _ASCII_NAME.fullmatch(name) else re.escape(name)
        )
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE)
    if stripped == title:
        return title
    while True:
        cleaned = _EDGE_SEPARATORS.sub("", _WHITESPACE_RUN.sub(" ", _EMPTY_BRACKETS.sub("", stripped))).strip()
        if cleaned == stripped:
            break
        stripped = cleaned
    return stripped or title
