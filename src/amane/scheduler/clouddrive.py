"""CloudDrive webhook 路径分流: 虚拟 POSIX 路径 → 库本地路径."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..library import LibraryScan, cloud_covers, normalize_cloud_path, to_local_path

CloudDriveAction = Literal["create", "delete", "rename"]


@dataclass(frozen=True, slots=True)
class CloudDriveChange:
    action: CloudDriveAction
    is_dir: bool
    source_file: str
    destination_file: str = ""


@dataclass(frozen=True, slots=True)
class CloudDriveRoute:
    library_id: int
    cloud_path: str
    local_path: str
    recursive: bool
    scan: LibraryScan


def match_route(cloud_file: str, routes: list[CloudDriveRoute]) -> CloudDriveRoute | None:
    """最长 cloud_path 前缀. 未命中返回 None."""
    try:
        target = normalize_cloud_path(cloud_file)
    except ValueError:
        return None
    best: CloudDriveRoute | None = None
    for route in routes:
        if cloud_covers(route.cloud_path, target) and (best is None or len(route.cloud_path) > len(best.cloud_path)):
            best = route
    return best


def local_for(route: CloudDriveRoute, cloud_file: str) -> Path:
    return to_local_path(route.local_path, route.cloud_path, cloud_file)
