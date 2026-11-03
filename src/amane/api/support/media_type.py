"""资源响应的媒体类型.

资源字节来自第三方, 记录里的 MIME 也由 URL 后缀推出 — 两者都不能决定浏览器按什么类型解释.
先按文件头判定, 再退回扩展名白名单; 都不命中时按附件下载, 配合 ``nosniff`` 让可渲染类型 (HTML / SVG)
无法在 API 同源执行脚本.
"""

from __future__ import annotations

from pathlib import Path

EXTENSION_MEDIA_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
}

NOSNIFF = {"X-Content-Type-Options": "nosniff"}
"""播放代理另有一份同名常量: 两侧的中和策略独立 (那边按播放用途改写类型), 共享会把两件事绑在一起."""

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

_HEAD_BYTES = 16


def _sniff(head: bytes) -> str | None:
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


def sniff_media_type(path: Path) -> str | None:
    """按内容判定可渲染类型; 读不到文件时回退扩展名."""
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        head = b""
    return _sniff(head) or EXTENSION_MEDIA_TYPES.get(path.suffix.lower())


def media_type_and_headers(path: Path) -> tuple[str, dict[str, str]]:
    media_type = sniff_media_type(path)
    if media_type is None:
        return "application/octet-stream", {**NOSNIFF, "Content-Disposition": "attachment"}
    return media_type, dict(NOSNIFF)
