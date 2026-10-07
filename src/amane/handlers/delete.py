"""DELETE: 按用户确认过的清单删除文件与目录, 删除对应索引, 按需剪枝."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import structlog

from ..library import (
    MEDIA_EXTENSIONS,
    CleanupInventory,
    DeleteOutcome,
    DeleteTally,
    InventoryEntry,
    InventoryEntryKind,
    InventoryReason,
    InventoryStore,
    OrphanScan,
    ancestor_dirs,
    delete_target,
    prune_empty_dirs,
    same_path,
)
from ..library.cleanup.orphan import classify_junk
from ..utils.path import existing_disk_path, path_key
from ..utils.threads import in_thread, path_is_dir
from ._common import LibraryTaskLocks
from .models import DeletePayload, DeleteResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import Library
    from ..db.repository import Repository

logger = structlog.get_logger()


class DeleteHandler(TaskHandler[DeletePayload, DeleteResult]):
    """执行一份清单; 不重新扫描, 不重新生成清单.

    残留条目是唯一的例外: 它们描述的是「目录里没有正片」这一会变的事实, 而清单有 24 小时
    有效期, 因此执行前用同一个判定复验一次 — 扫描之后落进正片的目录会连同索引行一起被删.

    输入是清单标识与排除项, 执行集合恒为清单的子集: 清单之外的路径不可能被删除.
    同库执行期与 ORGANIZE 共用一把锁.
    """

    def __init__(
        self,
        repo: Repository,
        inventory_store: InventoryStore,
        config: HotSettings | None = None,
        *,
        library_locks: LibraryTaskLocks | None = None,
    ):
        super().__init__(payload_t=DeletePayload, result_t=DeleteResult)
        self._repo = repo
        self._inventory_store = inventory_store
        self._config = config
        self._library_locks = library_locks if library_locks is not None else LibraryTaskLocks()

    async def handle(self, payload: DeletePayload) -> TaskResult[DeleteResult]:
        inventory = self._inventory_store.get(payload.inventory_id)
        if inventory is None:
            return TaskResult(success=False, error="清单不存在或已过期, 请重新扫描后再确认")
        if inventory.library_id != payload.library_id:
            return TaskResult(success=False, error="清单与目标库不一致")

        library = await self._repo.get_library(payload.library_id)
        if library is None:
            return TaskResult(success=False, error=f"Library {payload.library_id} not found")
        assert library.id is not None
        library_root = Path(library.path)
        if not same_path(inventory.root, library_root):
            return TaskResult(success=False, error=f"库路径已变更: {inventory.root} → {library_root}")
        if not await path_is_dir(library_root):
            return TaskResult(success=False, error=f"Not a directory: {library.path}")

        lock = await self._library_locks.get(library.id)
        async with lock:
            # 复验在锁内: 同一份清单被两次提交时, 只有先拿到锁的那次能执行.
            if inventory.executed:
                return TaskResult(success=False, error="该清单已经执行过, 请重新扫描后再确认")
            return await self._execute(payload, inventory, library_root, library)

    async def _execute(
        self,
        payload: DeletePayload,
        inventory: CleanupInventory,
        library_root: Path,
        library: Library,
    ) -> TaskResult[DeleteResult]:
        excluded_keys = _path_keys(payload.exclude, root=library_root)
        targets = [
            entry
            for entry in inventory.entries
            if not _matches(entry.path, exact=excluded_keys, subtrees=excluded_keys)
        ]
        excluded = len(inventory.entries) - len(targets)
        # 容器条目 (残留目录) 展开为它的子条目: 自身不是删除目标, 删完由剪枝回收空目录.
        targets = [entry for entry in targets if not entry.expandable]
        orphan_scan = self._orphan_scan(library, library_root=library_root)
        tally = DeleteTally()
        removed_paths: list[Path] = []
        reverify_rejected = 0
        total = len(targets)
        await self.report_progress(0, total, "delete")
        for i, entry in enumerate(targets, start=1):
            if orphan_scan is not None and entry.reason is InventoryReason.ORPHAN:
                refusal = await _reverify(entry, orphan_scan=orphan_scan, library_root=library_root)
                if refusal is not None:
                    # 记成 failed 而不是跳过: 用户确认过的条目没有删除, 结果里必须看得见.
                    tally.record(DeleteOutcome(status="failed", error=refusal))
                    reverify_rejected += 1
                    logger.warning("delete orphan reverify rejected", path=str(entry.path), reason=refusal)
                    await self.report_progress(i, total, entry.path.name)
                    continue
            outcome = await delete_target(entry.path, library_root=library_root)
            tally.record(outcome)
            if outcome.status != "failed":
                removed_paths.append(entry.path)
            elif outcome.error:
                logger.warning("delete item failed", path=str(entry.path), error=outcome.error)
            await self.report_progress(i, total, entry.path.name)

        indexed = await self._delete_index_rows(inventory.library_id, removed_paths)
        pruned = 0
        if payload.prune_empty_dirs and removed_paths:
            candidates: set[Path] = set()
            for path in removed_paths:
                candidates.update(ancestor_dirs(path, library_root=library_root))
            if candidates:
                pruned = (await prune_empty_dirs(candidates, library_root=library_root)).removed
        await self.report_progress(total, total, "done")

        inventory.executed = True
        logger.info(
            "delete completed",
            inventory_id=inventory.inventory_id,
            library_id=inventory.library_id,
            deleted=tally.deleted,
            changed=tally.changed,
            failed=tally.failed,
            freed_bytes=tally.freed_bytes,
            hardlink_items=tally.hardlink_items,
            pruned_dirs=pruned,
            excluded=excluded,
            indexed=indexed,
            reverify_rejected=reverify_rejected,
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
                reverify_rejected=reverify_rejected,
            ),
        )

    def _orphan_scan(self, library: Library, *, library_root: Path) -> OrphanScan | None:
        """复验用的库设置; 没有配置来源 (旧构造方式) 时跳过复验."""
        if self._config is None:
            return None
        media_extensions = frozenset(self._config.watcher.media_extensions) or MEDIA_EXTENSIONS
        return OrphanScan.from_library(
            library_root=library_root,
            scope_dir=library_root,
            subtitle_extensions=library.subtitle_extensions,
            trailer_pattern=library.trailer_pattern,
            patterns=library.patterns,
            media_extensions=media_extensions,
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


@in_thread
def _reverify(entry: InventoryEntry, *, orphan_scan: OrphanScan, library_root: Path) -> str | None:
    """残留条目是否仍然成立; 不成立时返回原因.

    判定与扫描时同一个 (``orphan.py``), 只是数据来源从遍历换成就地读取:

    - 祖先的直接子项里出现媒体, 或子树里出现媒体、不可删除的子项、白名单外的文件, 都不再成立;
    - 目录条目复验整棵子树, 文件条目复验该文件本身 (它可能已被正片覆盖);
    - 冷静期不重复施加: 条目已经过用户确认, 再按时间否决只会让删除在无提示的情况下少做.
    """
    if entry.path.parent != library_root and _ancestor_has_media(entry.path.parent, orphan_scan=orphan_scan):
        return "目录的祖先里出现了媒体"
    if entry.kind is InventoryEntryKind.DIR:
        return _reverify_dir(entry.path, orphan_scan=orphan_scan)
    return _reverify_file(entry.path, orphan_scan=orphan_scan)


def _reverify_dir(directory: Path, *, orphan_scan: OrphanScan) -> str | None:
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError as exc:
        return f"目录无法读取: {exc}"
    for child in children:
        path = Path(child.path)
        try:
            child_stat = path.lstat()
        except OSError as exc:
            return f"子项无法读取: {exc}"
        if stat.S_ISDIR(child_stat.st_mode):
            refusal = _reverify_dir(path, orphan_scan=orphan_scan)
            if refusal is not None:
                return refusal
            continue
        if orphan_scan.is_media(path, trailer=orphan_scan.trailer_matcher()):
            return f"目录里出现了媒体: {path.name}"
        if classify_junk(path, is_dir=False) is not None:
            continue
        if not orphan_scan.is_companion(path):
            return f"目录里出现了无法解释的文件: {path.name}"
    return None


def _reverify_file(path: Path, *, orphan_scan: OrphanScan) -> str | None:
    try:
        disk_path = existing_disk_path(path, follow_symlinks=False)
    except OSError as exc:
        return f"文件无法读取: {exc}"
    if disk_path is None:
        # 已经不在磁盘上: 交给执行侧按「已不存在」记账, 不算复验拒绝.
        return None
    if orphan_scan.is_media(disk_path, trailer=orphan_scan.trailer_matcher()):
        return f"文件已被媒体覆盖: {path.name}"
    if not orphan_scan.is_companion(disk_path):
        return f"文件不再是附属文件: {path.name}"
    return None


def _ancestor_has_media(directory: Path, *, orphan_scan: OrphanScan) -> bool:
    """目录自身或其任一祖先的直接子项里是否有媒体; 读不到时按有媒体处理 (保守)."""
    current = directory
    while True:
        try:
            with os.scandir(current) as scanned:
                children = list(scanned)
        except OSError:
            return True
        for child in children:
            path = Path(child.path)
            try:
                if stat.S_ISDIR(path.lstat().st_mode):
                    continue
            except OSError:
                return True
            if orphan_scan.is_media(path, trailer=orphan_scan.trailer_matcher()):
                return True
        if current == orphan_scan.library_root or current.parent == current:
            return False
        current = current.parent
