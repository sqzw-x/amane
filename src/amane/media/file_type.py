"""按内容判定媒体类型.

判定顺序: 文件头魔数 → PIL 头部解析; 两者都给不出登记过的类型时返回 None.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

IMAGE_MIME_PREFIX = "image/"

OCTET_STREAM_MIME = "application/octet-stream"
"""``mime_type`` 为它时类型未定, 判定须按内容复核."""

_HEAD_BYTES = 16

_BMFF_BRANDS: dict[str, str] = {
    "avif": "image/avif",
    "avis": "image/avif",
    "isom": "video/mp4",
    "iso2": "video/mp4",
    "mp41": "video/mp4",
    "mp42": "video/mp4",
    "avc1": "video/mp4",
    "dash": "video/mp4",
    "M4V ": "video/mp4",
}


def sniff_head(head: bytes) -> str | None:
    """按文件头判定类型; 未登记的格式返回 None."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return _BMFF_BRANDS.get(head[8:12].decode("ascii", errors="replace"))
    return None


def sniff_file(path: Path) -> str | None:
    """读取文件头判定类型; 读不到文件或格式未登记时返回 None."""
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return None
    return sniff_head(head)


def _pil_media_type(source: Path | io.BytesIO) -> str | None:
    """按 PIL 能否解码判定; 只解析文件头, 不加载像素."""
    try:
        with Image.open(source) as img:
            fmt = img.format
    except Exception:  # 各插件对畸形头部抛出的异常类型不一致
        return None
    if fmt is None:
        return None
    return Image.MIME.get(fmt)


def detect_media_type(path: Path) -> str | None:
    """判定文件类型: 文件头 → PIL; 都不识别时返回 None."""
    return sniff_file(path) or _pil_media_type(path)


def detect_media_type_bytes(data: bytes) -> str | None:
    """判定字节类型: 文件头 → PIL; 都不识别时返回 None."""
    return sniff_head(data) or _pil_media_type(io.BytesIO(data))
