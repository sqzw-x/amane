"""桌面主机使用系统默认应用打开已校验的文件."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .utils.threads import in_thread


def can_open_desktop_file() -> bool:
    """Windows / macOS 主机提供本机打开能力."""
    return sys.platform in {"darwin", "win32"}


@in_thread
def open_desktop_file(path: Path) -> None:
    """使用桌面主机为该文件登记的默认应用打开文件."""
    if sys.platform == "win32":
        os.startfile(path)
        return

    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["/usr/bin/open", str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise OSError(f"系统打开命令退出码: {exc.returncode}") from exc
        return

    raise OSError("当前运行环境不支持本机打开文件")
