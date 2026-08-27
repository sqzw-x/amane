"""ORGANIZE 同目录字幕发现与分集配对."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..enums import MoveMode
from ..parsing import detect_cd, parse_file_info
from ..utils.extensions import MEDIA_EXTENSIONS
from .file import execute_organize
from .path_templates import resolve_subtitle_path

if TYPE_CHECKING:
    from ..db.models import Library, Metadata
    from ..parsing.file_info import FileInfo

logger = structlog.get_logger()


def discover_subtitles(
    video_path: Path,
    extensions: Sequence[str],
    video_cd: int | None,
) -> list[Path]:
    """发现视频同目录下的字幕文件.

    - 只看直接父目录, 不递归; 扩展名大小写不敏感.
    - 空扩展名列表 → 不发现.
    - 多个字幕全部返回, 不挑主字幕.
    - 目录里只有这一部视频时全部带走 (无需分集消歧).
    - 多部视频时只解析字幕文件名上的分集: 有 CD 的跟同号; 解析不出的跟第一集
      (同目录视频 FileInfo.cd 的最小值; 存在无 CD 视频时第一集为 None).
    """
    exts = {e.lower() for e in extensions}
    if not exts:
        return []
    parent = video_path.parent
    if not parent.is_dir():
        return []

    videos = _videos_in_dir(video_path)
    multi = len(videos) > 1
    first_cd = _first_cd(videos) if multi else video_cd

    found: list[Path] = []
    for child in parent.iterdir():
        if not child.is_file() or child == video_path:
            continue
        if child.suffix.lower() not in exts:
            continue
        if not multi:
            found.append(child)
            continue
        sub_cd = detect_cd(child.name)
        if _belongs(video_cd, sub_cd, first_cd):
            found.append(child)
    found.sort(key=lambda p: p.name.casefold())
    return found


def place_subtitles(
    sources: Sequence[Path],
    video_source: Path,
    video_dest: Path,
    library: Library,
    metadata: Metadata,
    file_info: FileInfo,
    mode: MoveMode,
    safe_dirs: Sequence[Path] = (),
) -> None:
    """把已发现的字幕按模板落到 video_dest 侧, 失败只记日志."""
    for sub in sources:
        dest = resolve_subtitle_path(
            library,
            metadata,
            sub,
            video_dir=video_dest.parent,
            source_path=video_source,
            file_info=file_info,
            safe_dirs=safe_dirs,
        )
        result = execute_organize(
            source=sub,
            target_dir=dest.parent,
            target_stem=dest.stem,
            mode=mode,
            suffix=dest.suffix,
        )
        if not result.success:
            logger.warning("subtitle organize failed", source=str(sub), dest=str(dest), error=result.error)


def _videos_in_dir(video_path: Path) -> list[Path]:
    parent = video_path.parent
    videos = [video_path]
    if not parent.is_dir():
        return videos
    for child in parent.iterdir():
        if child == video_path or not child.is_file():
            continue
        if child.suffix.lower() in MEDIA_EXTENSIONS:
            videos.append(child)
    return videos


def _first_cd(videos: Sequence[Path]) -> int | None:
    cds = [parse_file_info(p).cd for p in videos]
    numbered = [c for c in cds if c is not None]
    if len(numbered) != len(cds):
        return None
    return min(numbered)


def _belongs(video_cd: int | None, sub_cd: int | None, first_cd: int | None) -> bool:
    if sub_cd is None:
        return video_cd == first_cd
    return sub_cd == video_cd
