"""ORGANIZE 同目录字幕发现与分集配对."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..enums import MoveMode
from ..parsing import detect_cd
from ..utils.threads import in_thread
from .file import execute_organize
from .path_templates import resolve_subtitle_path

if TYPE_CHECKING:
    from ..db.models import Library, Metadata
    from ..parsing.file_info import FileInfo

logger = structlog.get_logger()


@in_thread
def discover_subtitles(
    video_path: Path,
    extensions: Sequence[str],
    video_cd: int | None,
) -> list[Path]:
    """发现视频同目录下的字幕文件.

    - 只看直接父目录, 不递归; 扩展名大小写不敏感.
    - 空扩展名列表 → 不发现.
    - 多个字幕全部返回, 不挑主字幕.
    - 只解析字幕文件名上的分集 (`detect_cd`, 不看目录): 有标记的跟当前视频同号;
      解析不出的: 当前视频无分集 (同分集) 或分集为 1 时一并带走.
    """
    exts = {e.lower() for e in extensions}
    if not exts:
        return []
    parent = video_path.parent
    if not parent.is_dir():
        return []

    found: list[Path] = []
    for child in parent.iterdir():
        if not child.is_file() or child == video_path:
            continue
        if child.suffix.lower() not in exts:
            continue
        if _belongs(video_cd, detect_cd(child.name)):
            found.append(child)
    found.sort(key=lambda p: p.name.casefold())
    return found


@in_thread
def place_subtitles(
    sources: Sequence[Path],
    video_source: Path,
    video_dest: Path,
    library: Library,
    metadata: Metadata,
    file_info: FileInfo,
    mode: MoveMode,
    safe_dirs: Sequence[Path] | None = (),
    link_dir: Path | None = None,
    link_name: str | None = None,
) -> None:
    """把已发现的字幕按模板落到 video_dest 侧, 失败只记日志."""
    for sub in sources:
        dest = resolve_subtitle_path(
            library,
            metadata,
            sub,
            video_dir=video_dest.parent,
            link_dir=link_dir,
            video_name=video_dest.stem,
            link_name=link_name,
            source_path=video_source,
            file_info=file_info,
            safe_dirs=safe_dirs,
        )
        result = execute_organize.sync(
            source=sub,
            target_dir=dest.parent,
            target_stem=dest.stem,
            mode=mode,
            suffix=dest.suffix,
        )
        if not result.success:
            logger.warning("subtitle organize failed", source=str(sub), dest=str(dest), error=result.error)


def _belongs(video_cd: int | None, sub_cd: int | None) -> bool:
    if sub_cd == video_cd:
        return True
    return video_cd == 1 and sub_cd is None
