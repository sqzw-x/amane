"""删除执行单元: 边界、符号链接、挂载点、剪枝与释放空间统计."""

from __future__ import annotations

import errno
import os
import unicodedata
from typing import TYPE_CHECKING

import pytest

from amane.library import DeleteTally, ancestor_dirs, delete_target, prune_empty_dirs
from amane.library import delete as delete_module

if TYPE_CHECKING:
    from pathlib import Path


def _delete(path: Path, library_root: Path, tally: DeleteTally, outside: tuple[Path, ...] = ()) -> DeleteTally:
    tally.record(delete_target.sync(path, library_root=library_root, outside_targets=outside))
    return tally


class TestDeleteTarget:
    def test_delete_file(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        target = lib / "a.mkv"
        target.write_bytes(b"0123456789")

        tally = _delete(target, lib, DeleteTally())

        assert not target.exists()
        assert (tally.deleted, tally.changed, tally.failed) == (1, 0, 0)
        assert tally.freed_bytes == 10
        assert tally.hardlink_items == 0

    def test_missing_target_is_changed(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()

        tally = _delete(lib / "gone.mkv", lib, DeleteTally())

        assert (tally.deleted, tally.changed, tally.failed) == (0, 1, 0)
        assert tally.freed_bytes == 0

    def test_delete_directory_removes_contents(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work" / "sub").mkdir(parents=True)
        (lib / "work" / "a.mkv").write_bytes(b"aaaa")
        (lib / "work" / "sub" / "b.srt").write_bytes(b"bb")

        tally = _delete(lib / "work", lib, DeleteTally())

        assert not (lib / "work").exists()
        assert (tally.deleted, tally.failed) == (1, 0)
        assert tally.freed_bytes == 6

    def test_broken_symlink_removes_link_only(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        link = lib / "dangling.mkv"
        link.symlink_to(lib / "nowhere.mkv")

        tally = _delete(link, lib, DeleteTally())

        assert not link.exists(follow_symlinks=False)
        assert tally.deleted == 1

    def test_symlinked_directory_keeps_target(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        outside = tmp_path / "outside"
        lib.mkdir()
        outside.mkdir()
        (outside / "keep.mkv").write_bytes(b"keep")
        link = lib / "linked"
        link.symlink_to(outside, target_is_directory=True)

        tally = _delete(link, lib, DeleteTally())

        assert not link.exists(follow_symlinks=False)
        assert (outside / "keep.mkv").read_bytes() == b"keep"
        assert tally.deleted == 1
        assert tally.freed_bytes == 0

    def test_recursion_does_not_follow_symlinked_directory(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        outside = tmp_path / "outside"
        (lib / "work").mkdir(parents=True)
        outside.mkdir()
        (outside / "keep.mkv").write_bytes(b"keep")
        (lib / "work" / "linked").symlink_to(outside, target_is_directory=True)
        (lib / "work" / "a.mkv").write_bytes(b"aaaa")

        tally = _delete(lib / "work", lib, DeleteTally())

        assert not (lib / "work").exists()
        assert (outside / "keep.mkv").read_bytes() == b"keep"
        assert tally.freed_bytes == 4

    def test_outside_library_refused(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        outside = tmp_path / "outside"
        lib.mkdir()
        outside.mkdir()
        target = outside / "keep.mkv"
        target.write_bytes(b"keep")

        tally = _delete(target, lib, DeleteTally())

        assert target.read_bytes() == b"keep"
        assert (tally.deleted, tally.failed) == (0, 1)

    def test_listed_outside_target_deleted(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        outside = tmp_path / "linktree"
        lib.mkdir()
        outside.mkdir()
        target = outside / "poster.jpg"
        target.write_bytes(b"poster")

        tally = _delete(target, lib, DeleteTally(), outside=(target,))

        assert not target.exists()
        assert tally.deleted == 1

    def test_unlisted_sibling_of_outside_target_refused(self, tmp_path: Path) -> None:
        """库外只认清单明列的路径, 同目录的其它文件不在授权范围内."""
        lib = tmp_path / "lib"
        outside = tmp_path / "linktree"
        lib.mkdir()
        outside.mkdir()
        listed = outside / "poster.jpg"
        other = outside / "fanart.jpg"
        listed.write_bytes(b"poster")
        other.write_bytes(b"fanart")

        tally = _delete(other, lib, DeleteTally(), outside=(listed,))

        assert other.read_bytes() == b"fanart"
        assert tally.failed == 1

    def test_library_root_refused(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "a.mkv").write_bytes(b"a")

        outcome = delete_target.sync(lib, library_root=lib)

        assert outcome.status == "failed"
        assert "library root" in (outcome.error or "")
        assert (lib / "a.mkv").exists()

    def test_trash_root_refused(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)
        (trash / "old.mkv").write_bytes(b"old")

        tally = _delete(trash, lib, DeleteTally())

        assert (trash / "old.mkv").exists()
        assert tally.failed == 1

    def test_trash_contents_deleted(self, tmp_path: Path) -> None:
        """只拒绝回收站目录自身; 其下条目由回收站面板处置."""
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)
        old = trash / "old.mkv"
        old.write_bytes(b"old")

        tally = _delete(old, lib, DeleteTally())

        assert not old.exists()
        assert trash.is_dir()
        assert tally.deleted == 1

    def test_nfd_name_deleted_via_nfc_path(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        name = unicodedata.normalize("NFD", "がくえん.mkv")
        on_disk = lib / name
        on_disk.write_bytes(b"x")

        tally = _delete(lib / unicodedata.normalize("NFC", name), lib, DeleteTally())

        assert not on_disk.exists()
        assert tally.deleted == 1

    def test_nested_mount_boundary_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        lib = tmp_path / "lib"
        (lib / "work" / "mounted").mkdir(parents=True)
        (lib / "work" / "a.mkv").write_bytes(b"aaaa")
        (lib / "work" / "mounted" / "outside.mkv").write_bytes(b"outside")
        mounted_ino = (lib / "work" / "mounted").stat().st_ino
        monkeypatch.setattr(delete_module, "_crosses_boundary", lambda st, *, dev: st.st_ino == mounted_ino)

        tally = _delete(lib / "work", lib, DeleteTally())

        assert (lib / "work" / "mounted" / "outside.mkv").read_bytes() == b"outside"
        assert (tally.deleted, tally.failed) == (0, 1)

    def test_mount_point_target_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        lib = tmp_path / "lib"
        (lib / "mounted").mkdir(parents=True)
        (lib / "mounted" / "a.mkv").write_bytes(b"aaaa")
        monkeypatch.setattr(delete_module, "_crosses_boundary", lambda st, *, dev: True)

        tally = _delete(lib / "mounted", lib, DeleteTally())

        assert (lib / "mounted" / "a.mkv").exists()
        assert (tally.deleted, tally.failed) == (0, 1)


class TestDeleteTally:
    def test_freed_bytes_requires_all_hardlinks(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        first = lib / "a.mkv"
        second = lib / "a(1).mkv"
        first.write_bytes(b"0123456789")
        os.link(first, second)

        partial = _delete(first, lib, DeleteTally())
        assert partial.freed_bytes == 0
        assert partial.hardlink_items == 1

        both = _delete(first, lib, _delete(second, lib, DeleteTally()))
        assert both.hardlink_items == 0
        assert both.freed_bytes == 10

    def test_failed_target_keeps_removed_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """目录删除中途失败: 已删文件仍计入统计, 目标记失败."""
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "a.mkv").write_bytes(b"aaaa")
        (lib / "work" / "b.mkv").write_bytes(b"bb")
        calls = 0
        real_rmdir = os.rmdir

        def flaky_rmdir(path: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.EBUSY, "busy")
            real_rmdir(path)

        monkeypatch.setattr(os, "rmdir", flaky_rmdir)

        tally = _delete(lib / "work", lib, DeleteTally())

        assert (tally.deleted, tally.failed) == (0, 1)
        assert tally.freed_bytes == 6


class TestAncestorDirs:
    @pytest.mark.parametrize(
        ("relative", "expected"),
        [
            ("incoming/a.mkv", ["incoming"]),
            ("incoming/deep/a.mkv", ["incoming/deep", "incoming"]),
            ("a.mkv", []),
            (".amane_trash/sub/a.mkv", [".amane_trash/sub"]),
            ("../outside/a.mkv", []),
        ],
    )
    def test_ancestors(self, tmp_path: Path, relative: str, expected: list[str]) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()

        got = ancestor_dirs(lib / relative, library_root=lib)

        assert [str(path.relative_to(lib)) for path in got] == expected

    def test_library_root_has_no_ancestors(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()

        assert ancestor_dirs(lib, library_root=lib) == []


class TestPruneEmptyDirs:
    def test_removes_chain_bottom_up(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        deep = lib / "incoming" / "deep"
        deep.mkdir(parents=True)
        candidates = ancestor_dirs(lib / "incoming" / "deep" / "a.mkv", library_root=lib)

        result = prune_empty_dirs.sync(candidates, library_root=lib)

        assert result.removed == 2
        assert not (lib / "incoming").exists()

    def test_non_empty_directory_kept(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "incoming").mkdir(parents=True)
        (lib / "incoming" / "keep.mkv").write_bytes(b"keep")

        result = prune_empty_dirs.sync([lib / "incoming"], library_root=lib)

        assert result.removed == 0
        assert result.failed == 0
        assert (lib / "incoming").is_dir()

    def test_only_passed_directories_removed(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "incoming" / "deep").mkdir(parents=True)

        result = prune_empty_dirs.sync([lib / "incoming" / "deep"], library_root=lib)

        assert result.removed == 1
        assert (lib / "incoming").is_dir()

    def test_root_and_trash_kept(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)

        result = prune_empty_dirs.sync([lib, trash], library_root=lib)

        assert result.removed == 0
        assert lib.is_dir()
        assert trash.is_dir()

    def test_missing_directory_ignored(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()

        result = prune_empty_dirs.sync([lib / "gone"], library_root=lib)

        assert (result.removed, result.failed) == (0, 0)

    def test_other_errors_counted_as_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        lib = tmp_path / "lib"
        (lib / "incoming").mkdir(parents=True)

        def denied(path: str) -> None:
            raise OSError(errno.EACCES, "denied")

        monkeypatch.setattr(os, "rmdir", denied)

        result = prune_empty_dirs.sync([lib / "incoming"], library_root=lib)

        assert (result.removed, result.failed) == (0, 1)
        assert (lib / "incoming").is_dir()


class TestTrashSubtreeGuard:
    def test_recursive_delete_refuses_trash_subtree(self, tmp_path: Path) -> None:
        """目录删除不深入 .amane_trash: 回收站不能被子目录的整目录删除顺手清掉."""
        lib = tmp_path / "lib"
        work = lib / "work"
        trash = work / ".amane_trash"
        trash.mkdir(parents=True)
        (trash / "old.mkv").write_bytes(b"old")
        (work / "a.mkv").write_bytes(b"aaaa")

        tally = _delete(work, lib, DeleteTally())

        assert (tally.deleted, tally.failed) == (0, 1)
        assert (trash / "old.mkv").read_bytes() == b"old"
        assert work.is_dir()
