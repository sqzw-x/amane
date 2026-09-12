"""Builtin local source: 解析目标的根约束与同目录字幕的可用性."""

from pathlib import Path

import pytest

from amane.parsing.file_info import ContentType
from amane.playback.local import _find_sidecar, _resolved_allowed
from amane.plugins.api import PlaybackMediaFile


def _media_file(video: Path, library: Path | None) -> PlaybackMediaFile:
    return PlaybackMediaFile(
        id=1,
        path=str(video),
        size=video.stat().st_size,
        content_type=ContentType.CENSORED,
        library_id=1,
        library_path=None if library is None else str(library),
    )


@pytest.mark.parametrize(
    ("target", "library", "safe_dirs", "allowed"),
    [
        ("library", "library", None, True),
        ("library", "library", [], True),
        ("library", None, [], False),
        ("library", "library", ["other"], True),
        ("other", "library", ["other"], True),
        ("other", "library", [], False),
    ],
)
def test_resolved_target_roots(
    tmp_path: Path,
    target: str,
    library: str | None,
    safe_dirs: list[str] | None,
    allowed: bool,
) -> None:
    """``safe_dirs`` 为 ``None`` 表示 ``ALLOW_ALL``; 为列表时库根与名单是「或」的关系.

    空名单仍放行库根内的文件, 只在库根也未知时拒绝. 名单外的解析目标一律拒绝.
    """
    library_dir = tmp_path / "library"
    other_dir = tmp_path / "other"
    library_dir.mkdir()
    other_dir.mkdir()
    in_library = library_dir / "movie.mp4"
    in_library.write_bytes(b"x")
    in_other = other_dir / "movie.mp4"
    in_other.write_bytes(b"x")

    video = in_library if target == "library" else in_other
    item = _media_file(video, library_dir if library == "library" else None)
    dirs = None if safe_dirs is None else [other_dir if name == "other" else library_dir for name in safe_dirs]
    assert _resolved_allowed(video.resolve(), item, dirs) is allowed


@pytest.mark.asyncio
async def test_sidecar_must_be_decodable(tmp_path: Path) -> None:
    """探测阶段只声明读得出来的字幕轨, 避免列表给出轨道而实际加载必定失败."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    video = library_dir / "movie.mp4"
    video.write_bytes(b"x")
    item = _media_file(video, library_dir)
    sidecar = library_dir / "movie.srt"

    sidecar.write_bytes(b"\xff\xfe\x9c\x80\x81")
    assert await _find_sidecar(item, video.resolve(), [library_dir]) is None

    sidecar.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n中文字幕\n".encode("gbk"))
    assert await _find_sidecar(item, video.resolve(), [library_dir]) == sidecar.resolve()
