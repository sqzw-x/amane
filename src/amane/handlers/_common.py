"""
handler 间共享的可复用单元.

- scan_library: 扫库遍历 + 分类 (REFRESH / ORGANIZE); 规则见 library
- register_media_file: 注册 MediaFile (WATCHER 发现 / REFRESH 扫描); 不计算 oshash
- ensure_oshash: 按需计算并落库指纹 (仅 Stash 系刮削前调用)
- finalize_media_file: 标记 MediaFile 为已刮削并关联 Metadata (SCRAPE)

库目录落盘 (apply_file_operations) 在 file.py, 仅 ORGANIZE 调用.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..db import MediaFileStatus
from ..library import LibraryHit, LibraryScan
from ..utils.oshash import compute_oshash
from ..utils.threads import in_thread

if TYPE_CHECKING:
    from ..db.models import MediaFile
    from ..db.repository import Repository


def _maybe_file(f: Path) -> bool:
    """判断路径是否为(可能断链的)文件入口: 常规文件或符号链接."""
    return f.is_file() or f.is_symlink()


@in_thread
def scan_library(scan_dir: Path, *, recursive: bool, scan: LibraryScan) -> list[LibraryHit]:
    """遍历库目录, 按规则分类. 目录本身与回收站不产出."""
    glob_pattern = "**/*" if recursive else "*"
    hits: list[LibraryHit] = []
    for file_path in scan_dir.glob(glob_pattern):
        if not _maybe_file(file_path):
            continue
        kind = scan.classify(file_path)
        if kind is None:
            continue
        hits.append(LibraryHit(file_path, kind))
    return hits


async def register_media_file(repo: Repository, library_id: int, path: Path) -> MediaFile:
    """创建 MediaFile 记录. oshash 留给刮削按需计算, 注册不读文件内容."""
    return await repo.create_media_file(library_id=library_id, path=str(path))


async def ensure_oshash(repo: Repository, media: MediaFile) -> str | None:
    """已有指纹直接返回; 否则计算并落库. 失败留 None, 不阻断刮削."""
    if media.oshash is not None:
        return media.oshash
    media_hash = await compute_oshash(Path(media.path))
    if media_hash is None or media.id is None:
        return None
    updated = await repo.update_media_file(media.id, oshash=media_hash)
    return updated.oshash if updated is not None else media_hash


async def finalize_media_file(repo: Repository, media_file_id: int | None, metadata_id: int | None) -> None:
    """将 MediaFile 标记为已刮削并关联 Metadata. media_file_id 为 None 时静默跳过."""
    if media_file_id is None:
        return
    await repo.update_media_file(media_file_id, status=MediaFileStatus.SCRAPED, metadata_id=metadata_id)
