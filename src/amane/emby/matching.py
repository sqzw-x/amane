"""人物名归一与匹配: 归一后精确相等才算命中, 不做模糊匹配.

服务器的名字来自用户库里的文件名与元数据, 与 Amane 的 Actor 名称同源但形态可能不同 (全角/半角、
大小写、名字中的空格). 归一化把这些差异收敛, 因此「不同写法指向同一人」可以命中, 而相似但不同的
名字不会被误配 — 误配会把一个人的头像推到另一个人身上.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .client import EmbyPerson


def normalize_person_name(name: str) -> str:
    """去空白 + NFKC 归一 + 大小写折叠.

    NFKC 把兼容字符收敛到同一形态 (全角字母数字、半角片假名等); 空白含全角空格 U+3000, 因此
    ``希崎 ジェシカ`` 与 ``希崎ジェシカ`` 视为同名.
    """
    return "".join(unicodedata.normalize("NFKC", name).split()).casefold()


def match_persons(names: Sequence[str], persons: Iterable[EmbyPerson]) -> list[EmbyPerson]:
    """服务器人物里归一后命中任一 ``names`` 的条目; 按输入顺序去重, 保序."""
    wanted = {normalize_person_name(name) for name in names if name.strip()}
    if not wanted:
        return []
    matched: list[EmbyPerson] = []
    seen: set[str] = set()
    for person in persons:
        if person.id in seen or normalize_person_name(person.name) not in wanted:
            continue
        seen.add(person.id)
        matched.append(person)
    return matched
