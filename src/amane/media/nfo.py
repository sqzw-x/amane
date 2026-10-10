"""Kodi ``<movie>`` NFO, 供 Emby / Jellyfin / Kodi 读取. 正文由库的 NFO 内容模板渲染."""

from typing import TYPE_CHECKING

import aiofiles
import structlog

from ..organize.nfo_content import render_nfo_content

if TYPE_CHECKING:
    from pathlib import Path

    from ..db.models import Metadata
    from ..parsing.file_info import FileInfo


logger = structlog.get_logger()


async def write_nfo(
    metadata: Metadata,
    nfo_path: Path,
    *,
    content_template: str | None = None,
    file_info: FileInfo | None = None,
    video_name: str = "",
) -> bool:
    """content_template 为空时使用默认模板. 模板渲染结果不是良构 XML 时不写文件, 返回 False."""
    try:
        body = render_nfo_content(content_template, metadata, file_info=file_info, video_name=video_name)
    except ValueError as exc:
        logger.warning("nfo content rejected", number=metadata.number, path=str(nfo_path), error=str(exc))
        return False

    try:
        nfo_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(nfo_path, "w", encoding="UTF-8") as f:
            await f.write(body)

        logger.debug("nfo written", path=str(nfo_path))
        return True

    except Exception:
        logger.exception("nfo write failed", path=str(nfo_path))
        return False
