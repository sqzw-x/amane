"""资源响应的媒体类型.

资源字节来自第三方, 记录里的媒体类型不参与响应判定. 先按文件头判定, 再退回扩展名白名单;
都不命中时按附件下载, 配合 ``nosniff`` 让可渲染类型 (HTML / SVG) 无法在 API 同源执行脚本.
"""

from __future__ import annotations

from pathlib import Path

from ...media.file_type import sniff_file

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


def sniff_media_type(path: Path) -> str | None:
    """按内容判定可渲染类型; 读不到文件或格式未登记时回退扩展名."""
    return sniff_file(path) or EXTENSION_MEDIA_TYPES.get(path.suffix.lower())


def media_type_and_headers(path: Path) -> tuple[str, dict[str, str]]:
    media_type = sniff_media_type(path)
    if media_type is None:
        return "application/octet-stream", {**NOSNIFF, "Content-Disposition": "attachment"}
    return media_type, dict(NOSNIFF)
