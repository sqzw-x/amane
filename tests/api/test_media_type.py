"""资源响应的类型判定: 文件头优先, 扩展名白名单兜底."""

from pathlib import Path

import pytest

from amane.api.support.media_type import media_type_and_headers, sniff_media_type

_HTML = b"<html><script>alert(1)</script></html>"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
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
def test_sniff_by_content(tmp_path: Path, head: bytes, expected: str | None):
    path = tmp_path / "resource.bin"
    path.write_bytes(head)

    assert sniff_media_type(path) == expected


def test_extension_fallback(tmp_path: Path):
    """内容不可识别时按扩展名白名单, 让无魔数的图片格式仍能内联."""
    path = tmp_path / "poster.png"
    path.write_bytes(b"\x00\x00")

    assert sniff_media_type(path) == "image/png"


def test_unknown_extension_stays_unknown(tmp_path: Path):
    path = tmp_path / "page.html"
    path.write_bytes(_HTML)

    assert sniff_media_type(path) is None


def test_missing_file_falls_back_to_extension():
    assert sniff_media_type(Path("/nonexistent/poster.png")) == "image/png"


def test_renderable_type_gets_nosniff(tmp_path: Path):
    path = tmp_path / "poster.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0")

    media_type, headers = media_type_and_headers(path)

    assert media_type == "image/jpeg"
    assert headers == {"X-Content-Type-Options": "nosniff"}


def test_non_renderable_type_downloaded_as_attachment(tmp_path: Path):
    path = tmp_path / "page.html"
    path.write_bytes(_HTML)

    media_type, headers = media_type_and_headers(path)

    assert media_type == "application/octet-stream"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Disposition"] == "attachment"
