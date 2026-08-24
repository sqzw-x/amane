import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def is_descendant(p: str | Path, parent: str | Path) -> bool:
    """
    检查 p 是否是 parent 或者 parent 的后代.

    Raises:
        OSError: 存在循环的符号链接, 无访问权限等
    """
    p = os.path.realpath(p, strict=os.path.ALLOW_MISSING)
    parent = os.path.realpath(parent, strict=os.path.ALLOW_MISSING)
    # parent = /foo/bar, p = /foo/barbar 使得简单的前缀判断失效
    try:
        common = os.path.commonpath([p, parent])
    except ValueError:
        # Windows 上 p, parent 来自不同盘符时 commonpath 抛 ValueError
        return False
    return common == str(parent)


def is_any_descendant(p: str | Path, *parents: str | Path) -> bool:
    return any(is_descendant(p, parent) for parent in parents)
