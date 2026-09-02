"""跳过/黑名单正则与 `.amane_trash` 保留目录: 匹配文件名 (含扩展名)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from amane.db.models import Library
from amane.library.rules import (
    compile_skip_patterns,
    is_in_trash,
    is_skipped_media,
    is_undersized_video,
    is_video_media,
    normalize_subtitle_extensions,
    validate_blacklist_pattern,
    validate_subtitle_extension,
    validate_trailer_pattern,
)

SKIP_CASES = [
    ("(?i)trailer", "trailer.mp4", True),
    ("(?i)trailer", "MIDV-123-trailer.mkv", True),
    ("(?i)trailer", "TRAILER.MP4", True),
    ("(?i)trailer", "MIDV-123.mp4", False),
    ("预告", "中文预告片.mp4", True),
    ("预告", "MIDV-123.mp4", False),
    ("", "trailer.mp4", False),
    ("(?i)trailer", "sample.mp4", False),
]


@pytest.mark.parametrize(("pattern", "name", "skipped"), SKIP_CASES)
def test_is_skipped_media(pattern: str, name: str, skipped: bool):
    assert is_skipped_media(Path("/lib") / name, pattern) is skipped


def test_validate_trailer_pattern_rejects_invalid():
    with pytest.raises(ValueError, match="invalid trailer_pattern"):
        validate_trailer_pattern("[unclosed")


def test_library_rejects_invalid_trailer_pattern():
    with pytest.raises(ValidationError):
        Library.model_validate({"name": "x", "path": "/m", "trailer_pattern": "[unclosed"})


def test_validate_blacklist_pattern_rejects_invalid():
    with pytest.raises(ValueError, match="invalid blacklist_pattern"):
        validate_blacklist_pattern("(ads")


def test_validate_blacklist_pattern_allows_empty():
    assert validate_blacklist_pattern("") == ""


def test_library_rejects_invalid_blacklist_item():
    with pytest.raises(ValidationError):
        Library.model_validate({"name": "x", "path": "/m", "blacklist_patterns": ["广告", "(ads"]})


def test_compile_skip_patterns_combined():
    """多正则合并编译: 任一命中即匹配, 空项忽略."""
    compiled = compile_skip_patterns(["广告", "(?i)ads", ""])
    assert compiled is not None
    assert any(r.search("新片广告.mp4") for r in compiled)
    assert any(r.search("ADS_01.mkv") for r in compiled)
    assert not any(r.search("MIDV-123.mp4") for r in compiled)
    assert not any(r.search("trailer.mp4") for r in compiled)


def test_compile_skip_patterns_empty_or_invalid_returns_none():
    assert compile_skip_patterns([]) is None
    assert compile_skip_patterns(["", "   "]) is None
    assert compile_skip_patterns(None) is None
    # 写入时已校验, 此处仅为防御
    assert compile_skip_patterns(["[unclosed"]) is None


TRASH_CASES = [
    ("/lib/.amane_trash/ad.mp4", True),
    ("/lib/sub/.amane_trash/other/ad.mp4", True),
    ("/lib/.amane_trash", True),
    ("/lib/ad.mp4", False),
    ("/lib/not-trash/ad.mp4", False),
]


@pytest.mark.parametrize(("path", "in_trash"), TRASH_CASES)
def test_is_in_trash(path: str, in_trash: bool):
    assert is_in_trash(Path(path)) is in_trash


EXT_CASES = [
    (".srt", ".srt"),
    ("SRT", ".srt"),
    (" .Ass ", ".ass"),
]


@pytest.mark.parametrize(("raw", "expected"), EXT_CASES)
def test_validate_subtitle_extension(raw: str, expected: str):
    assert validate_subtitle_extension(raw) == expected


@pytest.mark.parametrize("raw", ["", ".", ".srt.ass", "../srt", "s r t", ".srt/x"])
def test_validate_subtitle_extension_rejects(raw: str):
    with pytest.raises(ValueError, match="subtitle extension"):
        validate_subtitle_extension(raw)


def test_normalize_subtitle_extensions_dedupes_and_allows_empty():
    assert normalize_subtitle_extensions(["SRT", ".srt", "ass"]) == [".srt", ".ass"]
    assert normalize_subtitle_extensions([]) == []


def test_library_rejects_invalid_subtitle_extension():
    with pytest.raises(ValidationError):
        Library.model_validate({"name": "x", "path": "/m", "subtitle_extensions": [".srt", "bad ext"]})


def test_library_rejects_negative_min_file_size():
    with pytest.raises(ValidationError):
        Library.model_validate({"name": "x", "path": "/m", "min_file_size": -1})


def test_is_video_media_uses_scan_extensions(tmp_path: Path):
    assert is_video_media(tmp_path / "a.mp4") is True
    assert is_video_media(tmp_path / "a.nfo") is False
    assert is_video_media(tmp_path / "a.srt") is False
    assert is_video_media(tmp_path / "a.jpg") is False
    assert is_video_media(tmp_path / "a.strm") is True
    assert is_video_media(tmp_path / "a.m2ts", media_extensions=frozenset({".m2ts"})) is True
    assert is_video_media(tmp_path / "a.mp4", media_extensions=frozenset({".m2ts"})) is False


SIZE_CASES = [
    ("a.mp4", 10, 50, True, "below threshold"),
    ("a.mp4", 50, 50, False, "equal to threshold stays"),
    ("a.mp4", 51, 50, False, "above threshold"),
    ("a.mp4", 10, 0, False, "threshold 0 disables"),
    ("a.nfo", 10, 50, False, "nfo is not video"),
    ("a.srt", 10, 50, False, "subtitle is not video"),
    ("a.jpg", 10, 50, False, "image is not video"),
    ("a.strm", 10, 50, False, "strm pointer is not sized"),
]


@pytest.mark.parametrize(("name", "size", "min_size", "undersized", "_"), SIZE_CASES)
def test_is_undersized_video(tmp_path: Path, name: str, size: int, min_size: int, undersized: bool, _: str):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    assert is_undersized_video(path, min_size) is undersized


def test_is_undersized_video_follows_symlink_target(tmp_path: Path):
    large = tmp_path / "real.mp4"
    large.write_bytes(b"x" * 100)
    link = tmp_path / "link.mp4"
    link.symlink_to("real.mp4")
    assert link.lstat().st_size < 50
    assert is_undersized_video(link, 50) is False


def test_is_undersized_video_symlink_to_small_target(tmp_path: Path):
    small = tmp_path / "ad.mp4"
    small.write_bytes(b"x" * 10)
    link = tmp_path / "link.mp4"
    link.symlink_to(small)
    assert is_undersized_video(link, 50) is True


def test_is_undersized_video_broken_symlink_is_not_undersized(tmp_path: Path):
    link = tmp_path / "broken.mp4"
    link.symlink_to(tmp_path / "missing.mp4")
    assert is_undersized_video(link, 50) is False


def test_is_undersized_video_stat_failure_is_not_undersized(tmp_path: Path):
    assert is_undersized_video(tmp_path / "missing.mp4", 10) is False
