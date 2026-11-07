"""按内容判定媒体类型: 文件头 → PIL."""

import io
from pathlib import Path

import pytest
from PIL import Image

from amane.media.file_type import detect_media_type, detect_media_type_bytes, sniff_file, sniff_head

_HTML = b"<html><script>alert(1)</script></html>"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
        (b"GIF87a" + b"\x00" * 10, "image/gif"),
        (b"GIF89a" + b"\x00" * 10, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4, "image/webp"),
        (b"\x00\x00\x00\x20ftypavif" + b"\x00" * 4, "image/avif"),
        (b"\x00\x00\x00\x20ftypisom" + b"\x00" * 4, "video/mp4"),
        # 未登记的 ISO BMFF 品牌与不可渲染内容都不给类型
        (b"\x00\x00\x00\x20ftypheic" + b"\x00" * 4, None),
        (_HTML, None),
        (b"", None),
    ],
)
def test_sniff_head(head: bytes, expected: str | None):
    assert sniff_head(head) == expected


def test_sniff_file_reads_leading_bytes(tmp_path: Path):
    path = tmp_path / "resource.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    assert sniff_file(path) == "image/png"


def test_sniff_missing_file():
    assert sniff_file(Path("/nonexistent/resource.bin")) is None


def _bmp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "BMP")
    return buf.getvalue()


def test_detect_falls_back_to_pil(tmp_path: Path):
    """文件头未登记的格式由 PIL 判定, 文件与字节两个入口一致."""
    path = tmp_path / "resource.bin"
    path.write_bytes(_bmp_bytes())

    assert detect_media_type(path) == "image/bmp"
    assert detect_media_type_bytes(_bmp_bytes()) == "image/bmp"


def test_detect_unknown_returns_none(tmp_path: Path):
    path = tmp_path / "resource.bin"
    path.write_bytes(_HTML)

    assert detect_media_type(path) is None
    assert detect_media_type_bytes(_HTML) is None
