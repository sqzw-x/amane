"""Vulkan ICD 探测 - 决定走上游 GPU zip 还是捆绑的 ncnn CPU 后端."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def has_vulkan_icd() -> bool:
    """是否存在可用的 Vulkan ICD (真 GPU 或 lavapipe).

    空目录不算. ``VK_ICD_FILENAMES`` 显式指定时视为有 (分隔符用 ``os.pathsep``,
    Windows 盘符 ``C:\\...`` 不能按 ``:`` 切开).
    macOS / Windows 的上游 zip 自带 MoltenVK / 系统 loader, 不走这条 Linux 探测.
    """
    explicit = os.environ.get("VK_ICD_FILENAMES", "").strip()
    if explicit:
        return any(Path(p).is_file() for p in explicit.split(os.pathsep) if p)

    if sys.platform in ("darwin", "win32"):
        return True

    roots = (
        Path("/usr/share/vulkan/icd.d"),
        Path("/etc/vulkan/icd.d"),
        Path("/usr/local/share/vulkan/icd.d"),
        Path.home() / ".local/share/vulkan/icd.d",
    )
    return any(root.is_dir() and any(root.glob("*.json")) for root in roots)
