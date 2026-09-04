from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..enums import ActorGender, MoveMode
from ..parsing import FileInfo, parse_file_info
from ..utils.threads import in_thread
from .file import execute_organize
from .path_templates import resolve_subtitle_path

if TYPE_CHECKING:
    from ..db.models import Library, Metadata

logger = structlog.get_logger()


@in_thread
def discover_subtitles(video_path: Path, extensions: Sequence[str], video: FileInfo) -> list[Path]:
    """只检查直接父目录, 不递归; 扩展名大小写不敏感. 空扩展名列表不发现. 多个字幕全部返回, 不挑主字幕.

    字幕只解析文件名 (`parse_file_info(text=...)`, 不依据目录、不使用路径回退).
    解析出番号时必须与当前视频番号相同 (忽略大小写), 再按分集配对;
    解析不出番号时回退独立目录规则: 有分集标记的跟当前视频同号, 解析不出的跟无分集或 CD1.
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
        if _belongs(video, parse_file_info(text=child.name)):
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
    actor_genders: Mapping[str, ActorGender] | None = None,
) -> None:
    """失败只记日志, 不抛异常."""
    for sub in sources:
        dest = resolve_subtitle_path(
            library,
            metadata,
            sub,
            video_dir=video_dest.parent,
            link_dir=link_dir,
            video_name=video_dest.stem,
            video_dest=video_dest,
            link_name=link_name,
            source_path=video_source,
            file_info=file_info,
            safe_dirs=safe_dirs,
            actor_genders=actor_genders,
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


def _belongs(video: FileInfo, sub: FileInfo) -> bool:
    if sub.number is not None and (video.number is None or sub.number.casefold() != video.number.casefold()):
        return False
    return _cd_belongs(video.cd, sub.cd)


def _cd_belongs(video_cd: int | None, sub_cd: int | None) -> bool:
    if sub_cd == video_cd:
        return True
    return video_cd == 1 and sub_cd is None
