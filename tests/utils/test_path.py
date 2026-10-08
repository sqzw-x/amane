import os
import sys
from pathlib import Path

import pytest

from amane.utils.path import (
    existing_disk_path,
    is_descendant,
    is_name_surrogate,
    is_name_surrogate_dir,
    is_resolved_path,
    nfc_path,
    path_forms,
    path_is_under,
    resolved_path,
)
from tests.helpers import make_junction


@pytest.mark.skipif(sys.platform == "win32", reason="此测试不适用于 Windows")
@pytest.mark.parametrize(
    "p, parent, expected",
    [
        # 基本场景
        ("/a/b/c", "/a/b", True),
        ("/a/b/c", "/a/b/./", True),
        ("/a/b", "/a/b", True),
        ("/a/b", "/a/b/", True),
        ("/a/b", "/a/b/.", True),
        ("/a/c", "/a/b", False),
        ("/a/b", "/a/b/c", False),
        ("/a/b/../c", "/a", True),
        ("/a/b/../c", "/a/c", True),
        ("/a/b/.", "/a/b", True),
        # 相对路径
        ("a/b/c", "a/b", True),
        ("a/b", "a/b", True),
        ("a/c", "a/b", False),
        ("a/c", "a/b/..", True),
        # Path 对象
        (Path("/a/b/c"), Path("/a/b"), True),
        (Path("a/b/c"), Path("a/b"), True),
        # 边界情况
        ("/a/barbar", "/a/bar", False),
        ("/a/bar", "/a/barbar", False),
        ("/", "/", True),
        ("/..", "/", True),
        ("/a", "/", True),
        # 混合类型
        (Path("/a/b/c"), "/a/b", True),
        ("/a/b/c", Path("/a/b"), True),
    ],
)
def test_is_descendant_posix(p, parent, expected):
    assert is_descendant(p, parent) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="此测试仅适用于 Windows 路径")
@pytest.mark.parametrize(
    "p, parent, expected",
    [
        ("C:\\Users\\Test", "C:\\Users", True),
        ("C:\\Users\\Test", "C:\\", True),
        ("C:\\Users\\Test", "D:\\Users", False),
        ("C:\\Users\\Test\\", "C:\\Users", True),
        ("C:\\Users\\Test", "C:\\Users\\", True),
        ("C:/Users/Test", "C:/Users", True),
        (Path("C:/Users/Test"), Path("C:/Users"), True),
        (Path("C:/Users/Test"), "C:/Users", True),
        ("C:/Users/Test", Path("C:/Users"), True),
    ],
)
def test_is_descendant_windows(p, parent, expected):
    assert is_descendant(p, parent) == expected


def test_is_descendant_no_propagation_on_resolution_error(monkeypatch):
    """realpath 抛 OSError (虚拟卷不支持规范化查询等) 不传播, 按字面继续比较 (issue #8)."""
    base = Path("/vault") / "media"

    def fake_realpath(path, *, strict=False):
        raise OSError(1, "Incorrect function")

    monkeypatch.setattr(os.path, "realpath", fake_realpath)
    assert is_descendant(base / "movies", base) is True
    assert is_descendant(Path("/elsewhere") / "movies", base) is False


def test_is_descendant_mixed_forms_fail_closed(monkeypatch):
    """盘符路径与设备路径前缀形式混合时按字面拒绝 (fail-closed), 不给边界判断留模糊空间."""

    def fake_realpath(path, *, strict=False):
        return os.fspath(path)

    monkeypatch.setattr(os.path, "realpath", fake_realpath)
    assert is_descendant(r"\\?\C:\Users\Test", r"C:\Users") is False


# じ: NFC = U+3058; NFD = し + 组合用浊点.
_NFC_JI = "\u3058"
_NFD_JI = "\u3057\u3099"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (f"/a/{_NFC_JI}.mp4", f"/a/{_NFC_JI}.mp4"),
        (f"/a/{_NFD_JI}.mp4", f"/a/{_NFC_JI}.mp4"),
        (f"/a/{_NFD_JI}/{_NFC_JI}.mp4", f"/a/{_NFC_JI}/{_NFC_JI}.mp4"),
        ("/ascii/foo.mp4", "/ascii/foo.mp4"),
        ("", ""),
    ],
)
def test_nfc_path(raw: str, want: str) -> None:
    assert nfc_path(raw) == want
    assert nfc_path(raw) == nfc_path(nfc_path(raw))


@pytest.mark.parametrize(
    ("path", "root", "want"),
    [
        ("/lib/show/a.mp4", "/lib/show", True),
        ("/lib/show", "/lib/show", True),
        ("/lib/show/nested/a.mp4", "/lib/show", True),
        ("/lib/show2/a.mp4", "/lib/show", False),
        ("/lib/showcase/a.mp4", "/lib/show", False),
        ("/lib/a.mp4", "/lib/show", False),
        ("/lib/show", "/lib/show/nested", False),
        (f"/lib/{_NFD_JI}/a.mp4", f"/lib/{_NFC_JI}", True),
    ],
)
def test_path_is_under(path: str, root: str, want: bool) -> None:
    assert path_is_under(path, root) is want


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("/a/foo.mp4", ("/a/foo.mp4",)),
        (f"/a/{_NFC_JI}.mp4", (f"/a/{_NFC_JI}.mp4", f"/a/{_NFD_JI}.mp4")),
        (f"/a/{_NFD_JI}.mp4", (f"/a/{_NFD_JI}.mp4", f"/a/{_NFC_JI}.mp4")),
    ],
)
def test_path_forms(raw: str, want: tuple[str, ...]) -> None:
    assert tuple(p.as_posix() for p in path_forms(raw)) == want


def test_existing_disk_path_tries_nfd(tmp_path: Path) -> None:
    """磁盘为 NFD 时, 用 NFC 查询仍返回可打开的路径."""
    nfd = tmp_path / f"{_NFD_JI}.mp4"
    nfd.write_bytes(b"x")
    nfc = tmp_path / f"{_NFC_JI}.mp4"
    found = existing_disk_path(nfc)
    assert found is not None
    assert found.read_bytes() == b"x"
    assert existing_disk_path(tmp_path / "missing.mp4") is None


class TestResolvedPath:
    def test_alias_resolves_to_target(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)

        assert resolved_path(alias) == real
        assert is_resolved_path(alias) is False
        assert is_resolved_path(real) is True

    def test_missing_path_folds_literal_segments(self, tmp_path: Path) -> None:
        """缺失路径仍做字面归一: ``..`` 被折叠, 不抛异常."""
        missing = tmp_path / "gone" / ".." / "gone"

        assert resolved_path(missing) == tmp_path / "gone"
        assert is_resolved_path(missing) is True

    def test_resolution_error_falls_back_to_literal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """规范化查询失败 (断连网络盘 / 虚拟卷) 时按字面绝对路径回退, 不抛异常."""
        # 路径须是平台绝对路径: 回退用 ``abspath``, 而 Windows 的 ntpath 会给 ``/vault/...`` 补上当前盘符.
        literal = tmp_path / "vault" / "media" / "gone"

        def fake_realpath(path, *, strict=False):
            raise OSError(1, "Incorrect function")

        monkeypatch.setattr(os.path, "realpath", fake_realpath)

        assert resolved_path(literal) == literal
        assert is_resolved_path(literal) is True


class TestNameSurrogate:
    def test_plain_directory_is_not_surrogate(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir"
        directory.mkdir()

        assert is_name_surrogate(directory.lstat()) is False
        assert is_name_surrogate_dir(directory) is False

    def test_symlinked_directory_is_not_surrogate(self, tmp_path: Path) -> None:
        """目录符号链接由 ``S_ISLNK`` 覆盖, 不走这一支."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        assert is_name_surrogate_dir(link) is False

    def test_missing_path_is_not_surrogate(self, tmp_path: Path) -> None:
        assert is_name_surrogate_dir(tmp_path / "gone") is False

    @pytest.mark.skipif(sys.platform != "win32", reason="junction 是 Windows 专有")
    def test_junction_is_surrogate(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "junction"
        make_junction(link, real)

        assert is_name_surrogate(link.lstat()) is True
        assert is_name_surrogate_dir(link) is True
