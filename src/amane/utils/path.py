import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _resolve_or_literal(p: str | Path) -> str:
    """解析为规范路径; 解析失败 (虚拟/重定向卷不支持查询等) 时按字面绝对路径兜底.

    必须使用 ``strict=False``: Windows 上 CloudDrive2 类虚拟卷或断连网络盘会使
    规范化查询失败 (WinError 1/1005 等), 严格模式会直接抛错. 非严格模式走 CPython
    容错路径 (按组件遍历), 不应向上传播异常. 兜底用 ``abspath`` 做 lexically 归一,
    保证 ``..`` 片段不会被字面比较误放行.
    """
    try:
        return os.path.realpath(p, strict=False)
    except OSError, ValueError:
        # 兜底需 lexically 归一且不做链接解析, Path.resolve() 语义不符
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


def relative_posix(p: str | Path, parent: str | Path) -> str | None:
    """p 相对 parent 的 POSIX 相对路径 (无前导 ``/``); p 不是 parent 的后代时返回 None.

    与 ``Path.relative_to`` 的区别: 先经 ``_resolve_or_literal`` 规范化两端, 因此库根或目标
    路径含符号链接、大小写不一致时仍能算出相对段; 且不抛异常, 由调用方决定越界语义.
    结果恒用 ``/`` 分隔, 供拼接远端 (OpenList / HTTP) 路径.
    """
    if not is_descendant(p, parent):
        return None
    return os.path.relpath(_resolve_or_literal(p), _resolve_or_literal(parent)).replace(os.sep, "/")
