"""oshash 计算单元测试: 与 oshash 包结果一致, 且边界/非法输入不抛异常."""

import os
import sys
from pathlib import Path

import pytest

from amane.utils.oshash import compute_oshash


def _rep_256(n: int) -> bytes:
    """0..255 循环字节串, 保证 64KiB 对齐后内容确定."""
    return bytes(range(256)) * (n // 256)


class TestComputeOshash:
    @pytest.mark.parametrize(
        "size,expected",
        [
            # 与 oshash 包一致 (预先用 0.1.1 计算): 大小 + 首尾 64KiB 小端 int64 累加
            (65536 * 2, "a0601fdf9f610000"),
            (204800, "a0601fdf9f622000"),
        ],
    )
    def test_matches_oshash_package(self, tmp_path: Path, size: int, expected: str):
        p = tmp_path / "video.mkv"
        p.write_bytes(_rep_256(size))
        assert compute_oshash(p) == expected

    @pytest.mark.parametrize("size", [0, 1, 65536, 131071])
    def test_too_small_returns_none(self, tmp_path: Path, size: int):
        p = tmp_path / "small.mp4"
        p.write_bytes(b"\x00" * size)
        assert compute_oshash(p) is None

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert compute_oshash(tmp_path / "nope.mp4") is None

    @pytest.mark.skipif(sys.platform == "win32", reason="创建符号链接需要特权, 行为不稳定")
    def test_symlink_to_valid_file(self, tmp_path: Path):
        target = tmp_path / "real.mkv"
        target.write_bytes(_rep_256(65536 * 2))
        link = tmp_path / "link.mkv"
        link.symlink_to(target)
        assert compute_oshash(link) == "a0601fdf9f610000"

    def test_empty_content_known_value(self, tmp_path: Path):
        """全零文件: 只有文件大小进入 hash, 首尾块贡献为 0."""
        p = tmp_path / "zeros.mkv"
        p.write_bytes(b"\x00" * (65536 * 2))
        assert compute_oshash(p) == f"{65536 * 2:016x}"

    def test_unreadable_file_returns_none(self, tmp_path: Path):
        if os.name == "nt":
            pytest.skip("POSIX 权限模型")
        p = tmp_path / "locked.mkv"
        p.write_bytes(_rep_256(65536 * 2))
        p.chmod(0)
        try:
            assert compute_oshash(p) is None
        finally:
            p.chmod(0o644)
