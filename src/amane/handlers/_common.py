from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..db import MediaFileStatus
from ..library import LibraryHit, LibraryScan
from ..utils.oshash import compute_oshash
from ..utils.threads import existing_disk_path, in_thread

if TYPE_CHECKING:
    from ..db.models import MediaFile
    from ..db.repository import Repository


def _maybe_file(f: Path) -> bool:
    """常规文件或符号链接 (含断链) 视为文件入口; 目录不产出."""
    return f.is_file() or f.is_symlink()


@in_thread
def scan_library(scan_dir: Path, *, recursive: bool, scan: LibraryScan) -> list[LibraryHit]:
    """目录本身与回收站不产出."""
    glob_pattern = "**/*" if recursive else "*"
    hits: list[LibraryHit] = []
    # 跳过目录与回收站; 其余按规则分类.
    for file_path in scan_dir.glob(glob_pattern):
        if not _maybe_file(file_path):
            continue
        kind = scan.classify(file_path)
        if kind is None:
            continue
        hits.append(LibraryHit(file_path, kind))
    return hits


async def register_media_file(repo: Repository, library_id: int, path: Path) -> MediaFile:
    """注册不读文件内容; oshash 留给刮削按需计算."""
    return await repo.create_media_file(library_id=library_id, path=str(path))


async def ensure_oshash(repo: Repository, media: MediaFile) -> str | None:
    """已有指纹直接返回; 计算失败留 None, 不阻断刮削."""
    if media.oshash is not None:
        return media.oshash
    disk = await existing_disk_path(Path(media.path))
    if disk is None:
        return None
    media_hash = await compute_oshash(disk)
    if media_hash is None or media.id is None:
        return None
    updated = await repo.update_media_file(media.id, oshash=media_hash)
    return updated.oshash if updated is not None else media_hash


async def finalize_media_file(repo: Repository, media_file_id: int | None, metadata_id: int | None) -> None:
    """media_file_id 为 None 时静默跳过."""
    if media_file_id is None:
        return
    await repo.update_media_file(media_file_id, status=MediaFileStatus.SCRAPED, metadata_id=metadata_id)
