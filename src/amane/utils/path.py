from __future__ import annotations

import os
import unicodedata
from pathlib import Path


def _resolve_or_literal(p: str | Path) -> str:
    """解析为规范路径; 解析失败 (虚拟/重定向卷不支持查询等) 时按字面绝对路径回退.

    必须使用 ``strict=False``: Windows 上 CloudDrive2 类虚拟卷或断连网络盘会使
    规范化查询失败 (WinError 1/1005 等), 严格模式会直接抛错. 非严格模式经 CPython
    容错路径 (按组件遍历), 不应向上传播异常. 回退用 ``abspath`` 做 lexically 归一,
    保证 ``..`` 片段不会被字面比较误放行.
    """
    try:
        return os.path.realpath(p, strict=False)
    except OSError, ValueError:
        # 回退需 lexically 归一且不做链接解析, Path.resolve() 语义不符
        return os.path.abspath(os.fspath(p))  # noqa: PTH100


def is_descendant(p: str | Path, parent: str | Path) -> bool:
    """
    检查 p 是否是 parent 或者 parent 的后代.

    解析失败 (符号链接循环, 无访问权限, 虚拟卷不支持规范化查询等) 时按字面路径
    继续比较, 不抛异常; 比较前统一 ``normcase`` 消除 Windows 大小写差异.
    """
    p = os.path.normcase(_resolve_or_literal(p))
    parent = os.path.normcase(_resolve_or_literal(parent))
    # parent = /foo/bar, p = /foo/barbar 使得简单的前缀判断失效
    try:
        common = os.path.commonpath([p, parent])
    except ValueError:
        # Windows 上 p, parent 来自不同盘符时 commonpath 抛 ValueError
        return False
    return common == parent


def is_any_descendant(p: str | Path, *parents: str | Path) -> bool:
    return any(is_descendant(p, parent) for parent in parents)


def nfc_path(path: str) -> str:
    """库内路径身份一律 NFC.

    MediaFile.path 的写入、按路径查找、与库内路径做集合差必须经过此函数.
    """
    return unicodedata.normalize("NFC", path)


def path_forms(path: str | Path) -> tuple[Path, ...]:
    """磁盘 I/O 候选: 传入形式, 再补规范等价的 NFC / NFD (去重, 保序)."""
    raw = os.fspath(path)
    forms: list[Path] = []
    seen: set[str] = set()
    for text in (raw, nfc_path(raw), unicodedata.normalize("NFD", raw)):
        if text in seen:
            continue
        seen.add(text)
        forms.append(Path(text))
    return tuple(forms)


def existing_disk_path(path: str | Path, *, follow_symlinks: bool = True) -> Path | None:
    """返回磁盘上存在的第一种形式. Linux / Windows 上 NFC 与 NFD 是不同文件名."""
    for candidate in path_forms(path):
        if candidate.exists(follow_symlinks=follow_symlinks):
            return candidate
    return None
