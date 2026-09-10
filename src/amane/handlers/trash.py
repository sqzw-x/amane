"""TRASH: 扫描范围内的黑名单与过小视频, 移入 `.amane_trash`."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings
from ..library import MEDIA_EXTENSIONS, TRASH_DIRNAME, LibraryFileKind, LibraryScan
from ..organize import MoveMode, execute_organize
from ..organize.file import OrganizeResult as DiskOrganizeResult
from ..utils.threads import in_thread, path_is_dir
from ._common import LibraryTaskLocks, scan_library
from .models import TrashPayload, TrashResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.models import Library
    from ..db.repository import Repository

logger = structlog.get_logger()


@in_thread
def _move_to_trash(file_path: Path, trash_dir: Path) -> DiskOrganizeResult:
    return execute_organize.sync(source=file_path, target_dir=trash_dir, target_stem=file_path.stem, mode=MoveMode.MOVE)


class TrashHandler(TaskHandler[TrashPayload, TrashResult]):
    """扫描磁盘, 将无效文件移入 `.amane_trash`; 不整理正片.

    同库执行期与 ORGANIZE 共用一把锁.
    """

    def __init__(self, repo: Repository, config: HotSettings, *, library_locks: LibraryTaskLocks | None = None):
        super().__init__(payload_t=TrashPayload, result_t=TrashResult)
        self._repo = repo
        self._config = config
        self._library_locks = library_locks if library_locks is not None else LibraryTaskLocks()

    async def handle(self, payload: TrashPayload) -> TaskResult[TrashResult]:
        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None
        library_root = Path(library.path)
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"Not a directory: {library.path}")
        scan_dir = Path(payload.path) if payload.path else library_root
        if not await path_is_dir(scan_dir):
            return TaskResult(success=False, error=f"Not a directory: {scan_dir}")

        lock = await self._library_locks.get(library.id)
        async with lock:
            return await self._handle_unlocked(payload, library, scan_dir)

    async def _handle_unlocked(
        self, payload: TrashPayload, library: Library, scan_dir: Path
    ) -> TaskResult[TrashResult]:
        recursive = payload.recursive if payload.recursive is not None else True
        media_extensions = frozenset(self._config.watcher.media_extensions) or MEDIA_EXTENSIONS
        await self.report_progress(0, 0, "scan")
        scan = LibraryScan(
            patterns=payload.patterns,
            trailer_pattern=library.trailer_pattern,
            blacklist_patterns=library.blacklist_patterns,
            min_file_size=library.min_file_size,
            media_extensions=media_extensions,
        )
        to_trash = [
            hit.path
            for hit in await scan_library(scan_dir, recursive=recursive, scan=scan)
            if hit.kind is LibraryFileKind.TRASH
        ]
        trashed, failed = await self._trash_files(library, to_trash)
        logger.info("trash completed", path=payload.path, trashed=trashed, failed=failed)
        return TaskResult(True, result=TrashResult(trashed=trashed, failed=failed))

    async def _trash_files(self, library: Library, files: Sequence[Path]) -> tuple[int, int]:
        """移至本库 `.amane_trash`. 固定物理移动, 不受 `move_mode` 影响."""
        if not files:
            await self.report_progress(1, 1, "done")
            return 0, 0
        trash_dir = Path(library.path) / TRASH_DIRNAME
        trashed = 0
        failed = 0
        trash_total = len(files)
        await self.report_progress(0, trash_total, "trash")
        for i, file_path in enumerate(files, start=1):
            result = await _move_to_trash(file_path, trash_dir)
            if not result.success:
                logger.warning("unwanted file trash failed", path=str(file_path), error=result.error)
                failed += 1
                await self.report_progress(i, trash_total, "trash")
                continue
            logger.info("unwanted file trashed", path=str(file_path), dest=str(result.dest))
            media_file = await self._repo.get_media_file_by_path(str(file_path))
            if media_file is not None:
                assert media_file.id is not None
                await self._repo.delete_media_file(media_file.id)
            trashed += 1
            await self.report_progress(i, trash_total, "trash")
        await self.report_progress(trash_total, trash_total, "done")
        return trashed, failed
