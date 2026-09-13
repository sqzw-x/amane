#!/usr/bin/env python3
"""打包用: 要收进包里的标准库顶层模块名.

PyInstaller 只收集主机**静态导入图**里的标准库模块; 插件是运行时从 data_dir 动态加载的,
打包器看不见它们引用什么. 主机自己不 import 的模块 (`html.parser`) 因此缺失, 只有桌面版会暴露.

两个构建脚本 (``build_macos_app.sh``、``build_windows_app.ps1``) 各自把这里的名字交给
``--collect-submodules``; 体积代价与排除项见 [docs/dev/desktop.md](../docs/dev/desktop.md).

用法: 构建脚本导入 ``stdlib_module_names()`` 并为每个名字生成一个 ``--collect-submodules``.
"""

from __future__ import annotations

import importlib.util
import sys

EXCLUDE = frozenset({"idlelib", "tkinter", "turtle", "turtledemo", "ensurepip"})
"""GUI 与安装器: 依赖无法随包提供的产物 (``_tkinter`` 原生扩展、Tcl/Tk 数据、ensurepip 的 wheel 副本)."""


def stdlib_module_names() -> tuple[str, ...]:
    """返回本平台要收集的标准库顶层模块名, 已排序去重.

    ``sys.stdlib_module_names`` 是全平台的并集 (macOS 上也有 ``winreg``), 因此再用
    ``find_spec`` 滤掉本平台不存在的名字 —— 那正是 ``collect_submodules`` 会告警的情况.
    """
    return tuple(
        sorted(
            name
            for name in sys.stdlib_module_names
            if not name.startswith("_")
            and "." not in name
            and name not in EXCLUDE
            and importlib.util.find_spec(name) is not None
        )
    )


if __name__ == "__main__":
    print("\n".join(stdlib_module_names()))  # noqa: T201 — 构建脚本按行读取这个名字名单
