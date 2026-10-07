"""path_validation: 存在性 / 类型 / safe_dirs 边界 (含 ALLOW_ALL)."""

from pathlib import Path

import pytest

from amane.api.support.path_validation import check_directory_path, check_plugin_install_path


@pytest.mark.parametrize(
    ("raw", "safe_dirs", "match"),
    [
        ("", [], "路径不能为空"),
        ("   ", None, "路径不能为空"),
    ],
)
def test_check_directory_path_rejects_empty(raw: str, safe_dirs: list[Path] | None, match: str):
    with pytest.raises(ValueError, match=match):
        check_directory_path.sync(raw, safe_dirs)


def test_check_directory_path_empty_list_unconfigured(tmp_path: Path):
    existing = tmp_path / "dir"
    existing.mkdir()
    with pytest.raises(ValueError, match="尚未配置安全目录"):
        check_directory_path.sync(str(existing), [])


def test_check_directory_path_allow_all_and_restricted(tmp_path: Path):
    inside = tmp_path / "in"
    outside = tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    as_file = inside / "f.txt"
    as_file.write_text("x")

    assert check_directory_path.sync(str(outside), None) == outside.resolve()
    assert check_directory_path.sync(str(inside), [inside]) == inside.resolve()

    with pytest.raises(ValueError, match="不在允许的安全目录内"):
        check_directory_path.sync(str(outside), [inside])
    with pytest.raises(ValueError, match="路径不存在"):
        check_directory_path.sync(str(inside / "nope"), None)
    with pytest.raises(ValueError, match="不是目录"):
        check_directory_path.sync(str(as_file), None)


def test_check_plugin_install_path_allow_all_zip(tmp_path: Path):
    z = tmp_path / "plugin.zip"
    z.write_bytes(b"PK")
    assert check_plugin_install_path.sync(str(z), None) == z.resolve()
    with pytest.raises(ValueError, match="不在允许的安全目录内"):
        check_plugin_install_path.sync(str(z), [tmp_path / "files"])
