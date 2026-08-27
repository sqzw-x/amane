"""跳过/黑名单正则与 `.amane_trash` 保留目录: 匹配文件名 (含扩展名)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from amane.db.models import Library
from amane.utils.extensions import (
    compile_skip_patterns,
    is_in_trash,
    is_skipped_media,
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
