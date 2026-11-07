"""资源响应的类型判定: 文件头优先, 扩展名白名单兜底."""

from pathlib import Path

from amane.api.support.media_type import media_type_and_headers, sniff_media_type

_HTML = b"<html><script>alert(1)</script></html>"


def test_content_wins_over_extension(tmp_path: Path):
    path = tmp_path / "resource.jpg"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    assert sniff_media_type(path) == "image/png"


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
