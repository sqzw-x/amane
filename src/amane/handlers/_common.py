"""
handler 间共享的可复用单元.

- iter_media_files: 目录遍历 + 媒体文件过滤 (REFRESH / ORGANIZE)
- register_media_file: 注册 MediaFile + 计算落库 oshash (WATCHER 发现 / REFRESH 扫描)
- finalize_media_file: 标记 MediaFile 为已刮削并关联 Metadata (SCRAPE)

库目录落盘 (apply_file_operations) 在 file.py, 仅 ORGANIZE 调用.
"""

from typing import TYPE_CHECKING

from ..db import MediaFileStatus
from ..utils.extensions import MEDIA_EXTENSIONS, compile_skip_patterns, is_in_trash
from ..utils.oshash import compute_oshash_async

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from ..db.models import MediaFile
    from ..db.repository import Repository


def _maybe_file(f: Path) -> bool:
    """判断路径是否为(可能断链的)文件入口: 常规文件或符号链接."""
    return f.is_file() or f.is_symlink()


def iter_media_files(
    scan_dir: Path,
    *,
    recursive: bool,
    patterns: list[str] | None,
    skip_patterns: Sequence[str | None] | None = None,
) -> Iterator[Path]:
    """遍历目录, 产出符合条件的媒体文件路径.

     过滤规则:
    - 仅产出常规文件 (跳过目录/目录符号链接等, 但允许文件符号链接和无效链接)
    - 提供 patterns 时按 glob 模式匹配 (任一命中即可)
    - 未提供 patterns 时按 MEDIA_EXTENSIONS 扩展名过滤
    - skip_patterns 任一命中文件名 (含扩展名) 则跳过 (预告片/黑名单正则)
    - 路径任一组件为 `.amane_trash` (回收站) 则跳过

     Args:
         scan_dir: 待遍历目录
         recursive: 是否递归子目录
         patterns: 文件名 glob 模式列表, None 时回退到扩展名过滤
         skip_patterns: 跳过正则列表 (预告片 + 黑名单), 空/非法则跳过
    """
    glob_pattern = "**/*" if recursive else "*"
    skip_res = compile_skip_patterns(skip_patterns)
    for file_path in scan_dir.glob(glob_pattern):
        if not _maybe_file(file_path):
            continue
        if is_in_trash(file_path):
            continue
        if skip_res is not None and any(r.search(file_path.name) for r in skip_res):
            continue
        if patterns:
            if not any(file_path.match(p) for p in patterns):
                continue
        elif file_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        yield file_path


async def register_media_file(repo: Repository, library_id: int, path: Path) -> MediaFile:
    """创建 MediaFile 记录并落库 oshash (计算失败留 None, 不阻断注册)."""
    media = await repo.create_media_file(library_id=library_id, path=str(path))
    media_hash = await compute_oshash_async(path)
    if media_hash is not None:
        assert media.id is not None
        return await repo.update_media_file(media.id, oshash=media_hash) or media
    return media


async def finalize_media_file(repo: Repository, media_file_id: int | None, metadata_id: int | None) -> None:
    """将 MediaFile 标记为已刮削并关联 Metadata. media_file_id 为 None 时静默跳过."""
    if media_file_id is None:
        return
    await repo.update_media_file(media_file_id, status=MediaFileStatus.SCRAPED, metadata_id=metadata_id)
