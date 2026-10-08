"""SCAN_INVALID: 只读遍历, 产出无效文件、残留目录与「扫描时已空」目录的清单."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings
from ..library import MEDIA_EXTENSIONS, InventoryStore, LibraryScan, OrphanScan, scan_inventory
from ..utils.threads import path_is_dir
from .models import ScanInvalidPayload, ScanInvalidResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()


class ScanInvalidHandler(TaskHandler[ScanInvalidPayload, ScanInvalidResult]):
    """遍历范围内的无效文件与空目录, 写入清单存放; 不移动、不删除、不改索引.

    只读, 因此不参与库锁: 与整理并发时清单可能落后于磁盘, 由执行侧逐项复验兜底.
    """

    def __init__(self, repo: Repository, config: HotSettings, inventory_store: InventoryStore):
        super().__init__(payload_t=ScanInvalidPayload, result_t=ScanInvalidResult)
        self._repo = repo
        self._config = config
        self._inventory_store = inventory_store

    async def handle(self, payload: ScanInvalidPayload) -> TaskResult[ScanInvalidResult]:
        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"媒体库 {payload.library_id} 不存在")
        assert library.id is not None
        library_root = Path(library.path)
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"不是目录: {library.path}")
        scope_dir = Path(payload.path) if payload.path else library_root
        if not await path_is_dir(scope_dir):
            return TaskResult(success=False, error=f"不是目录: {scope_dir}")

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
        inventory = await scan_inventory(
            scope_dir,
            library_id=library.id,
            library_root=library_root,
            recursive=recursive,
            patterns=payload.patterns or [],
            scan=scan,
            orphan_scan=OrphanScan.from_library(
                library_root=library_root,
                scope_dir=scope_dir,
                subtitle_extensions=library.subtitle_extensions,
                trailer_pattern=library.trailer_pattern,
                patterns=payload.patterns or [],
                media_extensions=media_extensions,
            ),
        )
        self._inventory_store.put(inventory)
        await self.report_progress(1, 1, "done")
        logger.info(
            "scan invalid completed",
            path=payload.path,
            inventory_id=inventory.inventory_id,
            entries=len(inventory.entries),
            blocked_dirs=inventory.blocked.total,
        )
        return TaskResult(
            True,
            result=ScanInvalidResult(
                inventory_id=inventory.inventory_id,
                entries=len(inventory.entries),
                dirs=len(inventory.dirs),
                scope_path=str(inventory.scope_path) if inventory.scope_path is not None else None,
                truncated=inventory.truncated,
                skipped_dirs=inventory.skipped_dirs,
                skipped_files=inventory.skipped_files,
                blocked_dirs=inventory.blocked.total,
            ),
        )
