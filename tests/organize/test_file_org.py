"""测试文件整理 - 文件操作"""

import sys
from typing import TYPE_CHECKING

import pytest

from amane.enums import LinkMode
from amane.organize import MoveMode, create_video_link, execute_organize

if TYPE_CHECKING:
    from pathlib import Path


class TestExecuteOrganize:
    def test_move_file(self, tmp_path: Path):
        """移动模式会转移文件"""
        src = tmp_path / "src" / "MIDV-123.mp4"
        src.parent.mkdir()
        src.write_text("fake video content")

        target_dir = tmp_path / "output" / "Studio X" / "MIDV-123"
        target_file = target_dir / "MIDV-123.mp4"

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )

        assert result.success is True
        assert result.dest == target_file
        assert target_file.exists()
        assert not src.exists()

    def test_hardlink_file(self, tmp_path: Path):
        """硬链接模式创建链接, 保留原文件"""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("fake video content")

        target_dir = tmp_path / "output" / "MIDV-123"
        target_file = target_dir / "MIDV-123.mp4"

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.HARDLINK,
        )

        assert result.success is True
        assert target_file.exists()
        assert src.exists()  # 保留原文件
        assert src.stat().st_ino == target_file.stat().st_ino  # 相同 inode

    def test_collision_renames(self, tmp_path: Path):
        """目标文件已存在时添加 (1) 后缀"""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("new content")

        target_dir = tmp_path / "output"
        target_dir.mkdir()
        (target_dir / "MIDV-123.mp4").write_text("existing content")

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )

        assert result.success is True
        assert result.dest is not None
        assert result.dest.name == "MIDV-123(1).mp4"

    @pytest.mark.parametrize("mode", list(MoveMode))
    def test_already_at_dest_is_success(self, tmp_path: Path, mode: MoveMode):
        """源已在模板路径上时直接成功, 不改成 (1)."""
        target_dir = tmp_path / "output"
        target_dir.mkdir()
        dest = target_dir / "MIDV-123.mp4"
        dest.write_text("content")

        result = execute_organize.sync(
            source=dest,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=mode,
        )

        assert result.success is True
        assert result.dest == dest
        assert dest.read_text() == "content"
        assert not (target_dir / "MIDV-123(1).mp4").exists()

    def test_already_hardlinked_dest_is_success(self, tmp_path: Path):
        """源与 dest 不同路径但同一 inode 时也不碰撞改名."""
        src = tmp_path / "src.mp4"
        src.write_text("content")
        target_dir = tmp_path / "output"
        target_dir.mkdir()
        dest = target_dir / "MIDV-123.mp4"
        dest.hardlink_to(src)

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.HARDLINK,
        )

        assert result.success is True
        assert result.dest == dest
        assert not (target_dir / "MIDV-123(1).mp4").exists()

    def test_source_missing_returns_failure(self, tmp_path: Path):
        """源文件不存在时返回失败"""
        result = execute_organize.sync(
            source=tmp_path / "nonexistent.mp4",
            target_dir=tmp_path / "output",
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )
        assert result.success is False

    @pytest.mark.skipif(sys.platform == "win32", reason="符号链接行为在 Windows 下不一致")
    def test_symlink_broken_source_does_not_crash(self, tmp_path: Path):
        """断链符号链接作为 source 时不应崩溃 (原 source.resolve() 抛 RuntimeError 的回归).

        断链 symlink 的 exists() 为 False, 故走 source-missing 失败分支, 但关键是不抛.
        """
        broken = tmp_path / "broken.mp4"
        broken.symlink_to(tmp_path / "missing-target.mp4")
        result = execute_organize.sync(
            source=broken,
            target_dir=tmp_path / "output",
            target_stem="MIDV-123",
            mode=MoveMode.SYMLINK,
        )
        # 不抛即达标; 断链源被判为不存在, 返回失败
        assert result.success is False

    @pytest.mark.skipif(sys.platform == "win32", reason="符号链接行为在 Windows 下不一致")
    def test_symlink_valid_source(self, tmp_path: Path):
        """SYMLINK 模式对有效源创建符号链接, 不再因 resolve 崩溃."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("content")
        target_dir = tmp_path / "output"
        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.SYMLINK,
        )
        assert result.success is True
        assert result.dest is not None
        assert result.dest.is_symlink()


class TestCreateVideoLink:
    def test_strm_writes_target_path(self, tmp_path: Path):
        target = tmp_path / "lib" / "A.mp4"
        target.parent.mkdir()
        target.write_text("video")
        link = tmp_path / "emby" / "A.strm"
        result = create_video_link.sync(target, link, LinkMode.STRM)
        assert result.success is True
        assert result.dest == link
        assert link.read_text(encoding="utf-8") == f"{target}\n"

    def test_strm_idempotent(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        link = tmp_path / "A.strm"
        assert create_video_link.sync(target, link, LinkMode.STRM).success
        assert create_video_link.sync(target, link, LinkMode.STRM).success
        assert link.read_text(encoding="utf-8") == f"{target}\n"

    def test_strm_custom_content(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        link = tmp_path / "A.strm"
        assert create_video_link.sync(target, link, LinkMode.STRM, content="/rel/A.mp4\n").success
        assert link.read_text(encoding="utf-8") == "/rel/A.mp4\n"

    def test_strm_refuses_regular_file(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        occupied = tmp_path / "A.jpg"
        occupied.write_text("nope")
        result = create_video_link.sync(target, occupied, LinkMode.STRM)
        assert result.success is False
        assert occupied.read_text() == "nope"

    @pytest.mark.skipif(sys.platform == "win32", reason="符号链接行为在 Windows 下不一致")
    def test_symlink_points_at_target(self, tmp_path: Path):
        target = tmp_path / "lib" / "A.mp4"
        target.parent.mkdir()
        target.write_text("video")
        link = tmp_path / "emby" / "A.mp4"
        result = create_video_link.sync(target, link, LinkMode.SYMLINK)
        assert result.success is True
        assert result.dest is not None
        assert result.dest.is_symlink()
        assert result.dest.resolve() == target.resolve()

    @pytest.mark.skipif(sys.platform == "win32", reason="符号链接行为在 Windows 下不一致")
    def test_symlink_refuses_regular_file(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        occupied = tmp_path / "B.mp4"
        occupied.write_text("other")
        result = create_video_link.sync(target, occupied, LinkMode.SYMLINK)
        assert result.success is False
        assert occupied.read_text() == "other"
