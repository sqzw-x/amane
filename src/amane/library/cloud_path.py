"""CloudDrive 虚拟路径 (POSIX) 与库挂载路径的对应."""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath

from ..enums import LibraryIngest


def normalize_cloud_path(path: str) -> str:
    """规范为以 ``/`` 开头、无末尾斜杠的 POSIX 路径. 反斜杠视为分隔符.

    不允许 ``.`` / ``..`` 段. 空串非法.
    """
    raw = unicodedata.normalize("NFC", path.strip().replace("\\", "/"))
    if not raw:
        raise ValueError("cloud_path 不能为空")
    while "//" in raw:
        raw = raw.replace("//", "/")
    if not raw.startswith("/"):
        raise ValueError("cloud_path 必须是以 / 开头的 POSIX 路径")
    if any(part in (".", "..") for part in raw.split("/") if part):
        raise ValueError("cloud_path 不允许 . 或 .. 段")
    posix = PurePosixPath(raw)
    parts: list[str] = []
    for part in posix.parts:
        if part == "/":
            continue
        if part in (".", ".."):
            raise ValueError("cloud_path 不允许 . 或 .. 段")
        parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def optional_cloud_path(path: str | None) -> str | None:
    """空串视为未设置."""
    if path is None:
        return None
    stripped = path.strip()
    if not stripped:
        return None
    return normalize_cloud_path(stripped)


def resolve_ingest_cloud_path(ingest: LibraryIngest, cloud_path: str | None) -> str | None:
    """ingest=clouddrive 时必须有 cloud_path; native 时清除."""
    if ingest is LibraryIngest.CLOUDDRIVE:
        resolved = optional_cloud_path(cloud_path)
        if resolved is None or resolved == "/":
            raise ValueError("ingest=clouddrive 时必须填写 CloudDrive 虚拟路径 (cloud_path)")
        return resolved
    return None


def cloud_covers(root: str, file_path: str) -> bool:
    """``file_path`` 是否等于 ``root`` 或其子路径. 两侧须已经 ``normalize_cloud_path``."""
    if file_path == root:
        return True
    prefix = root if root.endswith("/") else root + "/"
    return file_path.startswith(prefix)


def cloud_paths_overlap(left: str, right: str) -> bool:
    """相同路径或互为前缀. 两侧须已经 ``normalize_cloud_path``."""
    return cloud_covers(left, right) or cloud_covers(right, left)


def to_local_path(local_root: str, cloud_root: str, cloud_file: str) -> Path:
    """把虚拟路径换成 ``Library.path`` 下的本机路径. 用 posix 段拼接, 不解析虚拟路径为 OS Path."""
    root = normalize_cloud_path(cloud_root)
    file_path = normalize_cloud_path(cloud_file)
    if not cloud_covers(root, file_path):
        raise ValueError("cloud_file 不在 cloud_root 下")
    rel = file_path[len(root) :].lstrip("/")
    base = Path(local_root)
    if not rel:
        return base
    return base.joinpath(*rel.split("/"))
