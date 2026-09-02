"""strm_content_template: 空则绝对路径; {video_relpath} 相对库根; 非法输入拒绝."""

from pathlib import Path

import pytest

from amane.organize.strm_content import render_strm_content, validate_strm_content_template


@pytest.mark.parametrize(
    ("template", "dest_rel", "root_name", "expected"),
    [
        (None, "StudioX/ABC-123.mp4", "lib", None),
        ("/{video_relpath}", "StudioX/ABC-123.mp4", "lib", "/StudioX/ABC-123.mp4\n"),
        ("/OD/VC/{video_relpath}", "n.mp4", "lib", "/OD/VC/n.mp4\n"),
    ],
)
def test_render_strm_content(
    tmp_path: Path, template: str | None, dest_rel: str, root_name: str, expected: str | None
) -> None:
    root = tmp_path / root_name
    dest = root / dest_rel
    if expected is None:
        assert render_strm_content(template, dest, root) == f"{dest}\n"
    else:
        assert render_strm_content(template, dest, root) == expected


def test_relpath_outside_library_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside library root"):
        render_strm_content("/{video_relpath}", tmp_path / "outside" / "A.mp4", tmp_path / "lib")


@pytest.mark.parametrize("raw", ["/{video_relpath}\n", "{number}", "/{video_relpath"])
def test_validate_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_strm_content_template(raw)
