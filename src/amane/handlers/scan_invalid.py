"""SCAN_INVALID: 只读遍历, 产出无效文件与「扫描时已空」目录的清单."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..config import HotSettings
from ..library import MEDIA_EXTENSIONS, LibraryScan, PlanStore, scan_plan
from ..utils.threads import path_is_dir
from .models import ScanInvalidPayload, ScanInvalidResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()


class ScanInvalidHandler(TaskHandler[ScanInvalidPayload, ScanInvalidResult]):
    """遍历范围内的无效文件与已空目录, 写入清单存放; 不移动、不删除、不改索引.

    只读, 因此不参与库锁: 与整理并发时清单可能落后于磁盘, 由执行侧逐项复验兜底.
    """

    def __init__(self, repo: Repository, config: HotSettings, plan_store: PlanStore):
        super().__init__(payload_t=ScanInvalidPayload, result_t=ScanInvalidResult)
        self._repo = repo
        self._config = config
        self._plan_store = plan_store

    async def handle(self, payload: ScanInvalidPayload) -> TaskResult[ScanInvalidResult]:
        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None
        library_root = Path(library.path)
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"Not a directory: {library.path}")
        scope_dir = Path(payload.path) if payload.path else library_root
        if not await path_is_dir(scope_dir):
            return TaskResult(success=False, error=f"Not a directory: {scope_dir}")

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
        plan = await scan_plan(
            scope_dir,
            library_id=library.id,
            library_root=library_root,
            recursive=recursive,
            patterns=payload.patterns or [],
            scan=scan,
        )
        self._plan_store.put(plan)
        await self.report_progress(1, 1, "done")
        logger.info("scan invalid completed", path=payload.path, plan_id=plan.plan_id, entries=len(plan.entries))
        return TaskResult(
            True,
            result=ScanInvalidResult(
                plan_id=plan.plan_id,
                entries=len(plan.entries),
                dirs=len(plan.dirs),
                scope_path=str(plan.scope_path) if plan.scope_path is not None else None,
                truncated=plan.truncated,
                skipped_dirs=plan.skipped_dirs,
                skipped_files=plan.skipped_files,
            ),
        )
