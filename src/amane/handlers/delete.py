"""DELETE: 按用户确认过的清单删除文件与目录, 删除对应索引, 按需剪枝."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..library import (
    DeleteTally,
    LibraryPlan,
    PlanEntry,
    PlanStore,
    ancestor_dirs,
    delete_target,
    prune_empty_dirs,
    same_path,
)
from ..utils.path import path_is_under
from ..utils.threads import path_is_dir
from ._common import LibraryTaskLocks
from .models import DeletePayload, DeleteResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()


class DeleteHandler(TaskHandler[DeletePayload, DeleteResult]):
    """执行一份清单; 不重新扫描, 不重新生成清单.

    输入是清单标识与排除项, 执行集合恒为清单的子集: 清单之外的路径不可能被删除.
    同库执行期与 ORGANIZE 共用一把锁.
    """

    def __init__(self, repo: Repository, plan_store: PlanStore, *, library_locks: LibraryTaskLocks | None = None):
        super().__init__(payload_t=DeletePayload, result_t=DeleteResult)
        self._repo = repo
        self._plan_store = plan_store
        self._library_locks = library_locks if library_locks is not None else LibraryTaskLocks()

    async def handle(self, payload: DeletePayload) -> TaskResult[DeleteResult]:
        plan = self._plan_store.get(payload.plan_id)
        if plan is None:
            return TaskResult(success=False, error="清单不存在或已过期, 请重新扫描后再确认")
        if plan.executed:
            return TaskResult(success=False, error="该清单已经执行过, 请重新扫描后再确认")
        if plan.library_id != payload.library_id:
            return TaskResult(success=False, error="清单与目标库不一致")

        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None
        library_root = Path(library.path)
        if not same_path(plan.root, library_root):
            return TaskResult(success=False, error=f"库路径已变更: {plan.root} → {library_root}")
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"Not a directory: {library.path}")

        lock = await self._library_locks.get(library.id)
        async with lock:
            return await self._execute(payload, plan, library_root)

    async def _execute(self, payload: DeletePayload, plan: LibraryPlan, library_root: Path) -> TaskResult[DeleteResult]:
        targets = [entry for entry in plan.entries if not _excluded(entry, payload.exclude, root=library_root)]
        excluded = len(plan.entries) - len(targets)
        tally = DeleteTally()
        removed_paths: list[Path] = []
        total = len(targets)
        await self.report_progress(0, total, "delete")
        for i, entry in enumerate(targets, start=1):
            outcome = await delete_target(entry.path, library_root=library_root, outside_targets=_outside_targets(plan))
            tally.record(outcome)
            if outcome.status != "failed":
                removed_paths.append(entry.path)
            elif outcome.error:
                logger.warning("delete item failed", path=str(entry.path), error=outcome.error)
            await self.report_progress(i, total, entry.path.name)

        indexed = await self._delete_index_rows(plan.library_id, removed_paths)
        pruned = 0
        if payload.prune_empty_dirs and removed_paths:
            candidates: set[Path] = set()
            for path in removed_paths:
                candidates.update(ancestor_dirs(path, library_root=library_root))
            if candidates:
                pruned = (await prune_empty_dirs(candidates, library_root=library_root)).removed
        await self.report_progress(total, total, "done")

        plan.executed = True
        logger.info(
            "delete completed",
            plan_id=plan.plan_id,
            library_id=plan.library_id,
            deleted=tally.deleted,
            changed=tally.changed,
            failed=tally.failed,
            freed_bytes=tally.freed_bytes,
            hardlink_items=tally.hardlink_items,
            pruned_dirs=pruned,
            excluded=excluded,
            indexed=indexed,
        )
        return TaskResult(
            True,
            result=DeleteResult(
                deleted=tally.deleted,
                changed=tally.changed,
                failed=tally.failed,
                freed_bytes=tally.freed_bytes,
                hardlink_items=tally.hardlink_items,
                pruned_dirs=pruned,
                excluded=excluded,
                indexed=indexed,
            ),
        )

    async def _delete_index_rows(self, library_id: int, targets: Sequence[Path]) -> int:
        """按路径删除已删目标的索引行; 目录目标按前缀. 不触碰 Metadata."""
        if not targets:
            return 0
        removed = 0
        for media in await self._repo.list_media_files(library_id=library_id, limit=None):
            if media.id is None:
                continue
            if any(path_is_under(media.path, target) for target in targets):
                await self._repo.delete_media_file(media.id)
                removed += 1
        return removed


def _outside_targets(plan: LibraryPlan) -> tuple[Path, ...]:
    return tuple(entry.path for entry in plan.entries if entry.outside)


def _excluded(entry: PlanEntry, exclude: Sequence[str], *, root: Path) -> bool:
    """排除项按路径分量匹配: 库内是清单库根下的相对路径, 库外是绝对路径等值."""
    for raw in exclude:
        candidate = Path(raw)
        if entry.outside:
            if same_path(entry.path, candidate):
                return True
            continue
        prefix = candidate if candidate.is_absolute() else root / candidate
        if path_is_under(entry.path, prefix):
            return True
    return False
