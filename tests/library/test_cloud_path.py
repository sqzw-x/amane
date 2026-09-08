"""CloudDrive 虚拟路径规范化与本地拼接."""

from pathlib import Path

import pytest

from amane.enums import LibraryIngest
from amane.library.cloud_path import (
    cloud_covers,
    cloud_paths_overlap,
    normalize_cloud_path,
    optional_cloud_path,
    resolve_ingest_cloud_path,
    to_local_path,
)

NORMALIZE_CASES = [
    ("/115open/云下载", "/115open/云下载"),
    ("/115open/云下载/", "/115open/云下载"),
    (r"/115open\foo", "/115open/foo"),
    ("//115open//a//b", "/115open/a/b"),
    (" /115open/x ", "/115open/x"),
]


@pytest.mark.parametrize(("raw", "want"), NORMALIZE_CASES)
def test_normalize_cloud_path(raw: str, want: str) -> None:
    assert normalize_cloud_path(raw) == want


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "115open/foo", "/115open/../x", "/115open/./x"],
)
def test_normalize_cloud_path_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_cloud_path(raw)


def test_optional_cloud_path_blank_is_none() -> None:
    assert optional_cloud_path(None) is None
    assert optional_cloud_path("") is None
    assert optional_cloud_path("  ") is None


def test_resolve_ingest_requires_cloud_path() -> None:
    with pytest.raises(ValueError, match="cloud_path"):
        resolve_ingest_cloud_path(LibraryIngest.CLOUDDRIVE, None)
    with pytest.raises(ValueError, match="cloud_path"):
        resolve_ingest_cloud_path(LibraryIngest.CLOUDDRIVE, "/")
    assert resolve_ingest_cloud_path(LibraryIngest.NATIVE, "/115open/x") is None
    assert resolve_ingest_cloud_path(LibraryIngest.CLOUDDRIVE, "/115open/云下载/") == "/115open/云下载"


COVER_CASES = [
    ("/115open/lib", "/115open/lib", True),
    ("/115open/lib", "/115open/lib/a.mp4", True),
    ("/115open/lib", "/115open/liberate/a.mp4", False),
    ("/115open/lib", "/115open/other/a.mp4", False),
]


@pytest.mark.parametrize(("root", "file_path", "want"), COVER_CASES)
def test_cloud_covers(root: str, file_path: str, want: bool) -> None:
    assert cloud_covers(root, file_path) is want


OVERLAP_CASES = [
    ("/115open/lib", "/115open/lib", True),
    ("/115open/lib", "/115open/lib/sub", True),
    ("/115open/lib/sub", "/115open/lib", True),
    ("/115open/a", "/115open/b", False),
    ("/115open/lib", "/115open/liberate", False),
]


@pytest.mark.parametrize(("left", "right", "want"), OVERLAP_CASES)
def test_cloud_paths_overlap(left: str, right: str, want: bool) -> None:
    assert cloud_paths_overlap(left, right) is want


def test_to_local_path_joins_posix_segments(tmp_path: Path) -> None:
    local = tmp_path / "mount"
    assert to_local_path(str(local), "/115open/lib", "/115open/lib") == local
    assert to_local_path(str(local), "/115open/lib", "/115open/lib/a/b.mp4") == local / "a" / "b.mp4"
