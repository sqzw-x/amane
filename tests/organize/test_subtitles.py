"""同目录字幕发现与分集配对."""

from pathlib import Path
from typing import NamedTuple

import pytest

from amane.library import DEFAULT_SUBTITLE_EXTENSIONS
from amane.organize import discover_subtitles


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


class _Case(NamedTuple):
    id: str
    files: tuple[str, ...]
    video: str
    video_cd: int | None
    extensions: tuple[str, ...]
    expected: tuple[str, ...]


CASES: tuple[_Case, ...] = (
    _Case(
        "single-video-keeps-all-names",
        ("MIDV-123.mp4", "chs.srt", "MIDV-123.zh.ass", "notes.txt"),
        "MIDV-123.mp4",
        None,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("chs.srt", "MIDV-123.zh.ass"),
    ),
    _Case(
        "empty-extensions-discovers-none",
        ("MIDV-123.mp4", "chs.srt"),
        "MIDV-123.mp4",
        None,
        (),
        (),
    ),
    _Case(
        "filters-by-configured-ext",
        ("MIDV-123.mp4", "a.srt", "b.ass", "c.vtt"),
        "MIDV-123.mp4",
        None,
        (".srt",),
        ("a.srt",),
    ),
    _Case(
        "suffix-case-insensitive",
        ("MIDV-123.mp4", "A.SRT"),
        "MIDV-123.mp4",
        None,
        (".srt",),
        ("A.SRT",),
    ),
    _Case(
        "does-not-recurse",
        ("MIDV-123.mp4", "subs/hidden.srt"),
        "MIDV-123.mp4",
        None,
        DEFAULT_SUBTITLE_EXTENSIONS,
        (),
    ),
    _Case(
        "cd1-takes-matching-and-unparsed",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD2.mp4", "a-CD1.srt", "b-CD2.ass", "chs.srt"),
        "MIDV-123-CD1.mp4",
        1,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("a-CD1.srt", "chs.srt"),
    ),
    _Case(
        "cd2-takes-only-matching",
        ("MIDV-123-CD1.mp4", "MIDV-123-CD2.mp4", "a-CD1.srt", "b-CD2.ass", "chs.srt"),
        "MIDV-123-CD2.mp4",
        2,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("b-CD2.ass",),
    ),
    _Case(
        "cd2-does-not-take-unparsed",
        ("MIDV-123-CD2.mp4", "chs.srt", "b-CD2.ass"),
        "MIDV-123-CD2.mp4",
        2,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("b-CD2.ass",),
    ),
    _Case(
        "unnumbered-takes-unparsed-not-other-cd",
        ("MIDV-123.mp4", "MIDV-123-CD2.mp4", "chs.srt", "b-CD2.ass"),
        "MIDV-123.mp4",
        None,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("chs.srt",),
    ),
    _Case(
        "cd1-ignores-sibling-trailer",
        ("MIDV-123-CD1.mp4", "trailer.mp4", "chs.srt", "a-CD1.srt"),
        "MIDV-123-CD1.mp4",
        1,
        DEFAULT_SUBTITLE_EXTENSIONS,
        ("a-CD1.srt", "chs.srt"),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_discover_subtitles(case: _Case, tmp_path: Path) -> None:
    folder = tmp_path / "inbox"
    for rel in case.files:
        _touch(folder / rel)
    video = folder / case.video
    found = discover_subtitles.sync(video, case.extensions, case.video_cd)
    assert [p.name for p in found] == list(case.expected)
