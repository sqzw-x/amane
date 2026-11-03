"""读取画面尺寸: 真实编码的视频返回编码尺寸, 读不出尺寸的输入一律返回 None 而不是抛异常."""

import wave
from pathlib import Path

import pytest

from amane.library import probe_video_size
from amane.parsing import VideoSize
from tests.helpers import write_video


@pytest.mark.parametrize(
    ("name", "width", "height"),
    [
        ("ABC-123.mp4", 320, 240),
        # 竖屏: 宽高按编码原样返回, 不交换
        ("ABC-123.mkv", 240, 320),
    ],
)
def test_probe_video_size_reads_encoded_size(tmp_path: Path, name: str, width: int, height: int) -> None:
    path = tmp_path / name
    write_video(path, width, height)
    assert probe_video_size.sync(path) == VideoSize(width, height)


def test_probe_video_size_unreadable_returns_none(tmp_path: Path) -> None:
    strm = tmp_path / "ABC-123.strm"
    strm.write_text("http://127.0.0.1:9/ABC-123.mp4")
    empty = tmp_path / "ABC-123.mp4"
    empty.write_bytes(b"")
    junk = tmp_path / "ABC-123.mkv"
    junk.write_bytes(b"\x00" * 4096)
    # 容器合法但没有画面流
    audio = tmp_path / "ABC-123.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)

    for path in (strm, empty, junk, audio, tmp_path / "missing.mp4", tmp_path):
        assert probe_video_size.sync(path) is None, path
