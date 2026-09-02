"""strm_content_template 校验与渲染."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from amane.api.models.libraries import LibraryCreateRequest
from amane.organize.strm_content import (
    normalize_strm_content_template,
    render_strm_content,
    validate_strm_content_template,
    video_relpath,
)


class TestNormalizeAndValidate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("/{video_relpath}", "/{video_relpath}"),
            ("  /{video_relpath}  ", "/{video_relpath}"),
        ],
    )
    def test_normalize(self, raw: str | None, expected: str | None):
        assert normalize_strm_content_template(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "/{video_relpath}",
            "https://host/{video_relpath}",
            "/OD/VC/{video_relpath}",
            "",
            "   ",
            "literal-only",
        ],
    )
    def test_validate_accepts(self, raw: str):
        assert validate_strm_content_template(raw) == raw.strip()

    @pytest.mark.parametrize(
        "raw",
        [
            "/{video_relpath}\n",
            "a\r\nb",
            "{number}",
            "{video_relpath}{studio}",
            "{video_path}",
            "/{video_relpath",
            "{video_relpath|x=y}",
        ],
    )
    def test_validate_rejects(self, raw: str):
        with pytest.raises(ValueError):
            validate_strm_content_template(raw)

    def test_create_request_rejects_newline_and_unknown(self, tmp_path: Path):
        base = {"path": str(tmp_path), "scan": False}
        with pytest.raises(ValidationError):
            LibraryCreateRequest.model_validate({**base, "strm_content_template": "/{video_relpath}\n"})
        with pytest.raises(ValidationError):
            LibraryCreateRequest.model_validate({**base, "strm_content_template": "{number}"})


class TestRender:
    def test_empty_writes_absolute_dest(self, tmp_path: Path):
        dest = tmp_path / "lib" / "A.mp4"
        assert render_strm_content(None, dest, tmp_path / "lib") == f"{dest}\n"
        assert render_strm_content("  ", dest, tmp_path / "lib") == f"{dest}\n"

    def test_relpath_posix_no_leading_slash(self, tmp_path: Path):
        root = tmp_path / "lib"
        dest = root / "StudioX" / "ABC-123" / "ABC-123-CD2.mp4"
        assert video_relpath(dest, root) == "StudioX/ABC-123/ABC-123-CD2.mp4"
        assert render_strm_content("/{video_relpath}", dest, root) == "/StudioX/ABC-123/ABC-123-CD2.mp4\n"

    def test_prefix_and_url(self, tmp_path: Path):
        root = tmp_path / "lib"
        dest = root / "n.mp4"
        assert render_strm_content("/OD/VC/{video_relpath}", dest, root) == "/OD/VC/n.mp4\n"
        dest = root / "OD" / "VC" / "n.mp4"
        assert render_strm_content("https://h/{video_relpath}", dest, root) == "https://h/OD/VC/n.mp4\n"

    def test_literal_without_placeholder_allows_outside(self, tmp_path: Path):
        dest = tmp_path / "outside" / "A.mp4"
        root = tmp_path / "lib"
        assert render_strm_content("https://fixed/path", dest, root) == "https://fixed/path\n"

    def test_relpath_outside_library_raises(self, tmp_path: Path):
        dest = tmp_path / "outside" / "A.mp4"
        root = tmp_path / "lib"
        with pytest.raises(ValueError, match="outside library root"):
            render_strm_content("/{video_relpath}", dest, root)

    def test_collision_suffix_kept(self, tmp_path: Path):
        root = tmp_path / "lib"
        dest = root / "ABC-123 (1).mp4"
        assert render_strm_content("/{video_relpath}", dest, root) == "/ABC-123 (1).mp4\n"
