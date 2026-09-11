"""Feed 忽略关键词: 字面量子串, 大小写不敏感."""

from __future__ import annotations

import re
from collections.abc import Sequence

_TAG = re.compile(r"<[^>]+>")
_MAX_KEYWORD_LEN = 64
_MAX_KEYWORDS = 50


def normalize_ignore_keywords(values: Sequence[str] | None) -> list[str]:
    """去空白、丢空串、按大小写不敏感去重; 超长或过多则报错."""
    if values is None:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        keyword = "".join(ch for ch in raw.strip() if ord(ch) >= 32)
        if not keyword:
            continue
        if len(keyword) > _MAX_KEYWORD_LEN:
            raise ValueError(f"忽略关键词最长 {_MAX_KEYWORD_LEN} 字符")
        key = keyword.casefold()
        if key in seen:
            continue
        if len(result) >= _MAX_KEYWORDS:
            raise ValueError(f"忽略关键词最多 {_MAX_KEYWORDS} 个")
        seen.add(key)
        result.append(keyword)
    return result


def item_matches_ignore_keywords(
    keywords: Sequence[str],
    *,
    title: str | None = None,
    number: str | None = None,
    description: str | None = None,
    item_key: str | None = None,
) -> bool:
    """任一关键词命中标题、番号、正文或 item_key 即为忽略. 空列表不命中."""
    if not keywords:
        return False
    haystack = "\n".join(
        part
        for part in (
            title or "",
            number or "",
            _TAG.sub(" ", description or ""),
            item_key or "",
        )
        if part
    ).casefold()
    if not haystack:
        return False
    return any(keyword.casefold() in haystack for keyword in keywords if keyword)
