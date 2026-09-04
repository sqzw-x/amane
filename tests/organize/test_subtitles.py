"""同目录字幕发现: 番号匹配与分集回退."""

from pathlib import Path
from typing import NamedTuple

import pytest

from amane.library import DEFAULT_SUBTITLE_EXTENSIONS
from amane.organize import discover_subtitles
from amane.parsing import parse_file_info


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


class _Case(NamedTuple):
    id: str
    files: tuple[str, ...]
    video: str
    extensions: tuple[str, ...]
    expected: tuple[str, ...]


CASES: tuple[_Case, ...] = (
    _Case(
        "single-video-keeps-all-names",
        ("MIDV-123.mp4", "chs.srt", "MIDV-123.zh.ass", "notes.txt"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("chs.srt", "MIDV-123.zh.ass"),
    ),
    _Case(
        "empty-extensions-discovers-none",
        ("MIDV-123.mp4", "chs.srt"),
        "MIDV-123.mp4",
        (),
        (),
    ),
    _Case(
        "filters-by-configured-ext",
        ("MIDV-123.mp4", "a.srt", "b.ass", "c.vtt"),
        "MIDV-123.mp4",
        (".srt",),
        ("a.srt",),
    ),
    _Case(
        "suffix-case-insensitive",
        ("MIDV-123.mp4", "A.SRT"),
        "MIDV-123.mp4",
        (".srt",),
        ("A.SRT",),
    ),
    _Case(
        "does-not-recurse",
        ("MIDV-123.mp4", "subs/hidden.srt"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        (),
    ),
    _Case(
        "cd1-takes-matching-and-unparsed",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD2.mp4", "a-CD1.srt", "b-CD2.ass", "chs.srt"),
        "MIDV-123-CD1.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("a-CD1.srt", "chs.srt"),
    ),
    _Case(
        "cd2-takes-only-matching",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD2.mp4", "a-CD1.srt", "b-CD2.ass", "chs.srt"),
        "MIDV-123-CD2.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("b-CD2.ass",),
    ),
    _Case(
        "cd2-does-not-take-unparsed",
        ("MIDV-123-CD2.mp4", "chs.srt", "b-CD2.ass"),
        "MIDV-123-CD2.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("b-CD2.ass",),
    ),
    _Case(
        "unnumbered-takes-unparsed-not-other-cd",
        ("MIDV-123.mp4", "MIDV-123-CD2.mp4", "chs.srt", "b-CD2.ass"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("chs.srt",),
    ),
    _Case(
        "cd1-ignores-sibling-trailer",
        ("MIDV-123-CD1.mp4", "trailer.mp4", "chs.srt", "a-CD1.srt"),
        "MIDV-123-CD1.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("a-CD1.srt", "chs.srt"),
    ),
    _Case(
        "flat-takes-only-same-number",
        ("ABC-123.mp4", "ABC-123.srt", "DEF-456.mp4", "DEF-456.srt"),
        "ABC-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("ABC-123.srt",),
    ),
    _Case(
        "rejects-other-catalog-number",
        ("MIDV-123.mp4", "SSIS-001.srt", "chs.srt"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("chs.srt",),
    ),
    _Case(
        "rejects-shorter-catalog-prefix",
        ("MIDV-123.mp4", "MIDV-12.ass"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        (),
    ),
    _Case(
        "same-number-wrong-cd-rejected",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD2.srt"),
        "MIDV-123-CD1.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        (),
    ),
    _Case(
        "same-number-same-cd-rejects-other",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD1.srt", "SSIS-001-CD1.srt"),
        "MIDV-123-CD1.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("MIDV-123-CD1.srt",),
    ),
    _Case(
        "number-match-is-case-insensitive",
        ("MIDV-123.mp4", "midv-123.srt"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("midv-123.srt",),
    ),
    _Case(
        "space-normalized-number-matches",
        ("MIDV-123.mp4", "MIDV 123.ass"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("MIDV 123.ass",),
    ),
    _Case(
        "digits-only-name-is-unparsed-fallback",
        ("MIDV-123.mp4", "012.ass"),
        "MIDV-123.mp4",
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("012.ass",),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_discover_subtitles(case: _Case, tmp_path: Path) -> None:
    folder = tmp_path / "inbox"
    for rel in case.files:
        _touch(folder / rel)
    video = folder / case.video
    found = discover_subtitles.sync(video, case.extensions, parse_file_info(video))
    assert [p.name for p in found] == list(case.expected)
