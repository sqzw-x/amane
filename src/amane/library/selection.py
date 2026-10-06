"""显式来源的清单展开: 由选中项求出该作品的完整足迹.

删除文件 = 媒体文件自身 + 按库路径模板反解出的刮削产物 + 同目录同名字幕;
删除文件与作品文件夹 = 该作品文件夹下的全部内容, 只在该目录仅含这一条媒体索引且不是库根时提供.
展开只收库根内的路径: 配置链接模板时产物落在库外的链接树, 那些不归本功能管.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..organize.path_templates import resolve_paths
from ..parsing import parse_file_info
from ..utils.path import existing_disk_path, path_is_under
from .delete import same_path
from .plan import (
    MAX_PLAN_ENTRIES,
    LibraryPlan,
    PlanEntry,
    PlanEntryKind,
    PlanReason,
    PlanSource,
    new_plan_id,
)
from .rules import DEFAULT_SUBTITLE_EXTENSIONS

if TYPE_CHECKING:
    from ..db.models import Library, MediaFile, Metadata

logger = structlog.get_logger()

_Add = Callable[[Path], None]


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    """展开结果与未能纳入的部分 (面板据此提示用户)."""

    plan: LibraryPlan
    notices: list[str]


def build_selection_plan(
    *,
    library: Library,
    items: Sequence[MediaFile],
    indexed: Sequence[MediaFile],
    metas: Mapping[int, Metadata],
    include_work_dir: bool,
    limit: int = MAX_PLAN_ENTRIES,
) -> SelectionOutcome:
    """由选中的媒体文件展开显式来源清单; 只读磁盘, 不访问数据库."""
    assert library.id is not None
    root = Path(library.path)
    entries: list[PlanEntry] = []
    notices: list[str] = []

    def add(path: Path, *, indexed_file: bool = False) -> None:
        if len(entries) >= limit:
            return
        if any(same_path(path, entry.path) for entry in entries):
            return
        disk = existing_disk_path(path, follow_symlinks=False)
        if disk is None:
            notices.append(f"已不在磁盘上: {path}")
            return
        if not path_is_under(disk, root):
            if indexed_file:
                # 索引里的文件本该在库根内: 落在这里说明库路径与索引写法不一致 (旧库的符号链接别名).
                notices.append(f"文件不在库根内, 请重新保存媒体库路径: {disk}")
            # 模板产物落在库外链接树是正常的, 那些不归本功能管.
            return
        entries.append(_entry(disk))

    for item in items:
        add(Path(item.path), indexed_file=True)
        _add_products(library, item, metas, add=add, notices=notices)
        if not include_work_dir:
            _add_subtitles(library, Path(item.path), add=add)
            continue
        work_dir = Path(item.path).parent
        refusal = _work_dir_refusal(work_dir, root=root, indexed=indexed)
        if refusal is not None:
            notices.append(refusal)
            _add_subtitles(library, Path(item.path), add=add)
            continue
        _add_tree_contents(work_dir, add=add)

    plan = LibraryPlan(
        plan_id=new_plan_id(),
        library_id=library.id,
        root=root,
        scope_path=None,
        recursive=True,
        patterns=(),
        source=PlanSource.EXPLICIT,
        created_at=datetime.now(UTC),
        entries=entries,
    )
    logger.info("selection expanded", library_id=library.id, entries=len(plan.entries), notices=len(notices))
    return SelectionOutcome(plan=plan, notices=notices)


def _entry(disk: Path) -> PlanEntry:
    st = disk.lstat()
    if disk.is_symlink():
        kind = PlanEntryKind.SYMLINK
    elif disk.is_dir():
        kind = PlanEntryKind.DIR
    else:
        kind = PlanEntryKind.FILE
    is_file = kind is not PlanEntryKind.DIR
    return PlanEntry(
        path=disk,
        kind=kind,
        reason=PlanReason.EXPLICIT,
        size=st.st_size if is_file else None,
        dev=st.st_dev if is_file else None,
        ino=st.st_ino if is_file else None,
        nlink=st.st_nlink if is_file else None,
    )


def _add_products(
    library: Library,
    item: MediaFile,
    metas: Mapping[int, Metadata],
    *,
    add: _Add,
    notices: list[str],
) -> None:
    """按路径模板反解刮削产物: 产物位置由模板决定, 与视频文件名没有对应关系."""
    metadata = metas.get(item.metadata_id) if item.metadata_id is not None else None
    if metadata is None:
        return
    source = Path(item.path)
    try:
        paths = resolve_paths(
            library,
            metadata,
            ext=source.suffix.lstrip("."),
            source_path=source,
            file_info=parse_file_info(item.path),
            safe_dirs=None,
        )
    except ValueError as exc:
        notices.append(f"模板无法解析, 产物未纳入: {source} ({exc})")
        return
    for product in (paths.nfo, paths.thumb, paths.poster, paths.fanart, paths.trailer, paths.extrafanart_dir):
        # 模板产物本来就可能没写过, 只收磁盘上已有的, 缺失不值得提示.
        if existing_disk_path(product, follow_symlinks=False) is not None:
            add(product)


def _add_subtitles(library: Library, video: Path, *, add: _Add) -> None:
    """同目录内与视频同名或 `视频名.语言` 形式的字幕; 扩展名取库设置."""
    extensions = {ext.lower() for ext in (library.subtitle_extensions or DEFAULT_SUBTITLE_EXTENSIONS)}
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return
    for child in children:
        if child.suffix.lower() not in extensions:
            continue
        stem = child.stem
        if stem == video.stem or stem.startswith(f"{video.stem}."):
            add(child)


def _work_dir_refusal(work_dir: Path, *, root: Path, indexed: Sequence[MediaFile]) -> str | None:
    if same_path(work_dir, root):
        return "作品目录就是库根, 不提供整目录删除"
    siblings = [item for item in indexed if same_path(Path(item.path).parent, work_dir)]
    if len(siblings) > 1:
        return f"目录内有 {len(siblings)} 条媒体索引, 不提供整目录删除: {work_dir}"
    return None


def _add_tree_contents(directory: Path, *, add: _Add) -> None:
    """整目录删除按内容逐条展开: 面板给用户看的就是将被删除的全部路径."""
    try:
        children = list(directory.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_dir() and not child.is_symlink():
            _add_tree_contents(child, add=add)
            continue
        add(child)
