"""测试文件整理 - 文件操作"""

from pathlib import Path

import pytest

from amane.enums import LinkMode
from amane.organize import (
    MoveMode,
    PlaceOutcome,
    TargetState,
    create_video_link,
    execute_organize,
    target_state,
)


class TestTargetState:
    """目标占用判定: 读不出状态不得当作被占用."""

    @pytest.mark.parametrize(
        ("layout", "expected"),
        [
            ("missing", TargetState.FREE),
            ("other_file", TargetState.OCCUPIED),
            ("directory", TargetState.OCCUPIED),
            ("hardlink", TargetState.SAME),
            ("symlink", TargetState.SAME),
            ("dangling_symlink", TargetState.FREE),
        ],
    )
    def test_layouts(self, tmp_path: Path, layout: str, expected: TargetState):
        """逐种目标布局断言状态; 断链符号链接跟随符号链接后按空闲处理."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("source")
        dest = tmp_path / "output" / "MIDV-123.mp4"
        dest.parent.mkdir()
        match layout:
            case "other_file":
                dest.write_text("other")
            case "directory":
                dest.mkdir()
            case "hardlink":
                dest.hardlink_to(src)
            case "symlink":
                dest.symlink_to(src)
            case "dangling_symlink":
                dest.symlink_to(tmp_path / "gone.mp4")

        check = target_state.sync(src, dest)

        assert check.state is expected
        assert check.error is None

    def test_stat_failure_is_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """比较源与目标时 stat 失败记为 UNKNOWN, 错误说明带 errno 与失败路径.

        断言只取文件名: Windows 的 OSError 文本按 repr 转义路径里的反斜杠.
        """
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("source")
        dest = tmp_path / "output" / "MIDV-123.mp4"
        dest.parent.mkdir()
        dest.write_text("other")

        def denied(self: Path, other: object) -> bool:
            raise PermissionError(13, "Permission denied", str(other))

        monkeypatch.setattr(Path, "samefile", denied)

        check = target_state.sync(src, dest)

        assert check.state is TargetState.UNKNOWN
        assert check.error is not None
        assert "Permission denied" in check.error
        assert dest.name in check.error

    def test_dest_vanishing_between_reads_is_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """目标在存在性判定与比较之间消失 (竞态) 记为 UNKNOWN, 不当作空闲去落盘."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("source")
        dest = tmp_path / "output" / "MIDV-123.mp4"

        def always_found(path: Path, *, follow_symlinks: bool = True) -> Path:
            return path

        monkeypatch.setattr("amane.organize.file.existing_disk_path", always_found)

        check = target_state.sync(src, dest)

        assert check.state is TargetState.UNKNOWN
        assert check.error is not None
        assert dest.name in check.error


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

        assert result.outcome is PlaceOutcome.PLACED
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

        assert result.outcome is PlaceOutcome.PLACED
        assert target_file.exists()
        assert src.exists()  # 保留原文件
        assert src.stat().st_ino == target_file.stat().st_ino  # 相同 inode

    def test_dangling_symlink_at_target_is_treated_as_free(self, tmp_path: Path):
        """目标是断链符号链接时按空闲处理: 存在性判定跟随符号链接, MOVE 会替换掉它."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("content")
        target_dir = tmp_path / "output"
        target_dir.mkdir()
        dest = target_dir / "MIDV-123.mp4"
        dest.symlink_to(tmp_path / "missing-target.mp4")

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )

        assert result.outcome is PlaceOutcome.PLACED
        assert dest.is_file() and not dest.is_symlink()
        assert not src.exists()

        # SYMLINK 模式在同一位置会撞上 EEXIST: 记失败, 原来的符号链接保持原样.
        src2 = tmp_path / "MIDV-124.mp4"
        src2.write_text("content")
        target_dir2 = tmp_path / "output2"
        target_dir2.mkdir()
        dest2 = target_dir2 / "MIDV-124.mp4"
        dest2.symlink_to(tmp_path / "missing-target-2.mp4")

        result2 = execute_organize.sync(
            source=src2,
            target_dir=target_dir2,
            target_stem="MIDV-124",
            mode=MoveMode.SYMLINK,
        )

        assert result2.outcome is PlaceOutcome.FAILED
        assert dest2.is_symlink()
        assert src2.exists()

    @pytest.mark.parametrize("mode", list(MoveMode))
    def test_target_occupied_skips_without_touching_disk(self, tmp_path: Path, mode: MoveMode):
        """目标已被别的文件占用时不改名、不动磁盘, 由调用方记账."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("new content")

        target_dir = tmp_path / "output"
        target_dir.mkdir()
        dest = target_dir / "MIDV-123.mp4"
        dest.write_text("existing content")

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=mode,
        )

        assert result.outcome is PlaceOutcome.CONFLICT
        assert result.dest == dest
        assert dest.read_text() == "existing content"
        assert src.read_text() == "new content"
        assert [p.name for p in target_dir.iterdir()] == ["MIDV-123.mp4"]

    def test_unknown_target_state_is_failure_not_conflict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """读不出目标状态时记为失败并带上错误说明, 不计入冲突, 也不动磁盘上已有的文件."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("new content")
        target_dir = tmp_path / "output"
        target_dir.mkdir()
        dest = target_dir / "MIDV-123.mp4"
        dest.write_text("existing content")

        def denied(self: Path, other: object) -> bool:
            raise PermissionError(13, "Permission denied", str(other))

        monkeypatch.setattr(Path, "samefile", denied)

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )

        assert result.outcome is PlaceOutcome.FAILED
        assert result.error is not None
        assert "Permission denied" in result.error
        assert dest.read_text() == "existing content"
        assert src.read_text() == "new content"

    def test_target_is_directory_counts_as_occupied(self, tmp_path: Path):
        """目标位置是目录时按占用处理, 不抛异常."""
        src = tmp_path / "MIDV-123.mp4"
        src.write_text("content")
        target_dir = tmp_path / "output"
        dest = target_dir / "MIDV-123.mp4"
        dest.mkdir(parents=True)

        result = execute_organize.sync(
            source=src,
            target_dir=target_dir,
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )

        assert result.outcome is PlaceOutcome.CONFLICT
        assert dest.is_dir()
        assert src.exists()

    @pytest.mark.parametrize("mode", list(MoveMode))
    def test_already_at_dest_is_success(self, tmp_path: Path, mode: MoveMode):
        """源已在模板路径上时视为已就位, 不做任何改动."""
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

        assert result.outcome is PlaceOutcome.PLACED
        assert result.dest == dest
        assert dest.read_text() == "content"
        assert [p.name for p in target_dir.iterdir()] == ["MIDV-123.mp4"]

    def test_move_resolves_nfd_source_from_nfc_path(self, tmp_path: Path):
        """库内 NFC 路径对应磁盘 NFD 文件时仍能打开并移动."""
        nfd = "\u3057\u3099"
        nfc = "\u3058"
        src_nfd = tmp_path / "src" / f"{nfd}.mp4"
        src_nfd.parent.mkdir()
        src_nfd.write_text("video")
        result = execute_organize.sync(
            source=tmp_path / "src" / f"{nfc}.mp4",
            target_dir=tmp_path / "out",
            target_stem="SSIS-914",
            mode=MoveMode.MOVE,
        )
        assert result.outcome is PlaceOutcome.PLACED
        dest = tmp_path / "out" / "SSIS-914.mp4"
        assert dest.read_text() == "video"
        assert not src_nfd.exists()

    def test_already_hardlinked_dest_is_success(self, tmp_path: Path):
        """源与 dest 不同路径但同一 inode 时视为已就位, 不按占用处理."""
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

        assert result.outcome is PlaceOutcome.PLACED
        assert result.dest == dest

    def test_source_missing_returns_failure(self, tmp_path: Path):
        """源文件不存在时返回失败"""
        result = execute_organize.sync(
            source=tmp_path / "nonexistent.mp4",
            target_dir=tmp_path / "output",
            target_stem="MIDV-123",
            mode=MoveMode.MOVE,
        )
        assert result.outcome is PlaceOutcome.FAILED
        assert result.error is not None

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
        assert result.outcome is PlaceOutcome.FAILED

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
        assert result.outcome is PlaceOutcome.PLACED
        assert result.dest is not None
        assert result.dest.is_symlink()


class TestCreateVideoLink:
    def test_strm_writes_target_path(self, tmp_path: Path):
        target = tmp_path / "lib" / "A.mp4"
        target.parent.mkdir()
        target.write_text("video")
        link = tmp_path / "emby" / "A.strm"
        result = create_video_link.sync(target, link, LinkMode.STRM)
        assert result.outcome is PlaceOutcome.PLACED
        assert result.dest == link
        assert link.read_text(encoding="utf-8") == f"{target}\n"

    def test_strm_idempotent(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        link = tmp_path / "A.strm"
        assert create_video_link.sync(target, link, LinkMode.STRM).outcome is PlaceOutcome.PLACED
        assert create_video_link.sync(target, link, LinkMode.STRM).outcome is PlaceOutcome.PLACED
        assert link.read_text(encoding="utf-8") == f"{target}\n"

    def test_strm_custom_content(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        link = tmp_path / "A.strm"
        result = create_video_link.sync(target, link, LinkMode.STRM, content="/rel/A.mp4\n")
        assert result.outcome is PlaceOutcome.PLACED
        assert link.read_text(encoding="utf-8") == "/rel/A.mp4\n"

    def test_strm_refuses_regular_file(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        occupied = tmp_path / "A.jpg"
        occupied.write_text("nope")
        result = create_video_link.sync(target, occupied, LinkMode.STRM)
        assert result.outcome is PlaceOutcome.FAILED
        assert occupied.read_text() == "nope"

    def test_symlink_points_at_target(self, tmp_path: Path):
        target = tmp_path / "lib" / "A.mp4"
        target.parent.mkdir()
        target.write_text("video")
        link = tmp_path / "emby" / "A.mp4"
        result = create_video_link.sync(target, link, LinkMode.SYMLINK)
        assert result.outcome is PlaceOutcome.PLACED
        assert result.dest is not None
        assert result.dest.is_symlink()
        assert result.dest.resolve() == target.resolve()

    def test_symlink_idempotent(self, tmp_path: Path):
        """链接已指向同一目标时保持原样并成功."""
        target = tmp_path / "lib" / "A.mp4"
        target.parent.mkdir()
        target.write_text("video")
        link = tmp_path / "emby" / "A.mp4"
        assert create_video_link.sync(target, link, LinkMode.SYMLINK).outcome is PlaceOutcome.PLACED
        assert create_video_link.sync(target, link, LinkMode.SYMLINK).outcome is PlaceOutcome.PLACED
        assert link.is_symlink()
        assert link.resolve() == target.resolve()

    def test_symlink_refuses_regular_file(self, tmp_path: Path):
        target = tmp_path / "A.mp4"
        target.write_text("video")
        occupied = tmp_path / "B.mp4"
        occupied.write_text("other")
        result = create_video_link.sync(target, occupied, LinkMode.SYMLINK)
        assert result.outcome is PlaceOutcome.FAILED
        assert occupied.read_text() == "other"
