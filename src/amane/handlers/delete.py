"""DELETE: 按用户确认过的清单删除文件与目录, 删除对应索引, 按需剪枝."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import structlog

from ..library import (
    DeleteTally,
    LibraryPlan,
    PlanStore,
    ancestor_dirs,
    delete_target,
    prune_empty_dirs,
    same_path,
)
from ..utils.path import path_key
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
            # 复验在锁内: 同一份清单被两次提交时, 只有先拿到锁的那次能执行.
            if plan.executed:
                return TaskResult(success=False, error="该清单已经执行过, 请重新扫描后再确认")
            return await self._execute(payload, plan, library_root)

    async def _execute(self, payload: DeletePayload, plan: LibraryPlan, library_root: Path) -> TaskResult[DeleteResult]:
        excluded_keys = _path_keys(payload.exclude, root=library_root)
        targets = [
            entry for entry in plan.entries if not _matches(entry.path, exact=excluded_keys, subtrees=excluded_keys)
        ]
        excluded = len(plan.entries) - len(targets)
        tally = DeleteTally()
        removed_paths: list[Path] = []
        total = len(targets)
        await self.report_progress(0, total, "delete")
        for i, entry in enumerate(targets, start=1):
            outcome = await delete_target(entry.path, library_root=library_root)
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
        keys = _path_keys(targets, root=None)
        removed = 0
        for media in await self._repo.list_media_files(library_id=library_id, limit=None):
            if media.id is None:
                continue
            if _matches(media.path, exact=keys, subtrees=keys):
                await self._repo.delete_media_file(media.id)
                removed += 1
        return removed


def _path_keys(paths: Sequence[str | Path], *, root: Path | None) -> set[str]:
    """把目标或排除项归约成一个集合, 供 ``_matches`` 按分量命中.

    逐条目 × 逐目标的比较在万级规模下是数十分钟量级, 且跑在事件循环上 (DELETE 还持有同库锁).
    相对路径按清单库根解释; 文件路径放进前缀集合也无害 — 文件路径下没有子孙.
    """
    keys: set[str] = set()
    for raw in paths:
        candidate = Path(raw)
        if root is not None and not candidate.is_absolute():
            candidate = root / candidate
        keys.add(path_key(candidate))
    return keys


def _matches(path: str | Path, *, exact: set[str], subtrees: set[str]) -> bool:
    """路径是否命中集合: 与某项相等, 或落在某项之下 (逐级查父目录)."""
    key = PurePath(path_key(path))
    if os.fspath(key) in exact:
        return True
    return any(os.fspath(parent) in subtrees for parent in key.parents)
