"""DELETE: 按用户确认过的清单删除文件与目录, 删除对应索引, 按需剪枝."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import structlog

from ..library import (
    MEDIA_EXTENSIONS,
    CleanupInventory,
    DeleteOutcome,
    DeleteTally,
    InventoryEntry,
    InventoryReason,
    InventoryStore,
    OrphanScan,
    ancestor_dirs,
    delete_target,
    prune_empty_dirs,
    same_path,
)
from ..library.cleanup.orphan import JunkKind, classify_junk
from ..utils.path import path_key
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

    描述会变的事实的条目是例外, 执行前就地复验一次:

    - 残留条目: 清单有 24 小时有效期, 而「目录里没有正片」随时会变, 扫描之后落进正片的目录
      会连同索引行一起被删;
    - 空目录条目: 执行侧删目录是递归的, 后来落进去的内容会一起没.

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
            # 预检已全部通过, 从这里开始动盘: 清单随即作废, 取消 / 崩溃 / 中途失败都算.
            # 半执行的快照留在面板上只会显示一批磁盘上已经没有的条目, 而重跑它没有意义 —
            # 重跑既不会恢复已删的文件, 也不会补上没删的. 预检失败不置位: 一次都没动盘, 重试是合理的.
            inventory.executed = True
            return await self._execute(payload, inventory, library_root, library)

    async def _execute(
        self,
        payload: DeletePayload,
        inventory: CleanupInventory,
        library_root: Path,
        library: Library,
    ) -> TaskResult[DeleteResult]:
        excluded_keys = _path_keys(payload.exclude, root=library_root)
        included_keys = _path_keys(payload.include, root=library_root)
        targets = [
            entry
            for entry in inventory.entries
            if not _is_excluded(entry.path, excluded=excluded_keys, included=included_keys)
        ]
        excluded = len(inventory.entries) - len(targets)
        # 容器条目 (残留目录) 自身不是删除目标, 但它的子树要整体复验一次, 因此先记下路径.
        containers = {path_key(entry.path): entry.path for entry in inventory.entries if entry.expandable}
        targets = [entry for entry in targets if not entry.expandable]
        orphan_scan = self._orphan_scan(library, library_root=library_root, patterns=inventory.patterns)
        # 复验的探测按目录记忆: 同一目录下的条目走的是同一趟路, 网络盘上这是删除的主要开销.
        probe = _MediaProbe(orphan_scan=orphan_scan) if orphan_scan is not None else None
        tally = DeleteTally()
        removed_paths: list[Path] = []
        reverify_rejected = 0
        total = len(targets)
        await self.report_progress(0, total, "delete")
        for i, entry in enumerate(targets, start=1):
            if probe is not None:
                refusal = await _reverify(entry, probe=probe, container=_host_container(entry.path, containers))
                if refusal is not None:
                    # 记成 failed 而不是跳过: 用户确认过的条目没有删除, 结果里必须看得见.
                    tally.record(DeleteOutcome(status="failed", error=refusal))
                    reverify_rejected += 1
                    logger.warning("delete reverify rejected", path=str(entry.path), reason=refusal)
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

    def _orphan_scan(self, library: Library, *, library_root: Path, patterns: Sequence[str]) -> OrphanScan | None:
        """复验用的判定设置; 没有配置来源 (旧构造方式) 时跳过复验.

        ``patterns`` 取自清单本身而不是库的当前设置: 扫描侧用的是那次任务实际生效的值
        (``LibraryScanBase._apply_library`` 允许按任务覆盖), 复验用同一份设置才谈得上「同一套条件」.
        """
        if self._config is None:
            return None
        media_extensions = frozenset(self._config.watcher.media_extensions) or MEDIA_EXTENSIONS
        return OrphanScan.from_library(
            library_root=library_root,
            scope_dir=library_root,
            subtitle_extensions=library.subtitle_extensions,
            trailer_pattern=library.trailer_pattern,
            patterns=patterns,
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


def _is_excluded(path: str | Path, *, excluded: set[str], included: set[str]) -> bool:
    """路径是否被排除在删除集合之外.

    排除项与纳入项互为祖先时按最深的一条判定: 面板用「排除一个目录 + 纳入其中一项」表达
    「保留这个目录, 但删掉里面的某一项」, 用户刚点的那条一定更深.
    """
    return _deepest_depth(path, excluded) > _deepest_depth(path, included)


def _deepest_depth(path: str | Path, keys: set[str]) -> int:
    """命中路径的最深键的深度; 没命中返回 -1. ``PurePath.parents`` 由深到浅, 首个命中即最深."""
    key = PurePath(path_key(path))
    for candidate in (key, *key.parents):
        if os.fspath(candidate) in keys:
            return len(candidate.parts)
    return -1


@dataclass
class _MediaProbe:
    """一趟删除里的探测缓存.

    复验要把条目的祖先链逐级列一遍, 同一目录下的条目走的是同一趟路: 云下载库的 4421 个残留
    条目落在约 1095 个目录里, 按目录记住结果即可少列约四分之三的目录. 容器子树的复验同样
    只做一次 — 一个容器下的条目共用一个结论.
    """

    orphan_scan: OrphanScan
    levels: dict[Path, bool] = field(default_factory=dict)
    subtrees: dict[Path, str | None] = field(default_factory=dict)

    def level_has_media(self, directory: Path) -> bool:
        cached = self.levels.get(directory)
        if cached is None:
            cached = _level_has_media(directory, orphan_scan=self.orphan_scan)
            self.levels[directory] = cached
        return cached

    def ancestors_have_media(self, directory: Path) -> bool:
        """目录自身或其任一祖先的直接子项里是否有媒体; 读不到时按有媒体处理 (保守).

        只走到扫描范围那一层: 更上面的层扫描时没看过, 拿它否决会把清单里本来成立的条目全部拒掉.
        """
        scope = self.orphan_scan.scope_dir
        current = directory
        while True:
            if self.level_has_media(current):
                return True
            if current == scope or current.parent == current:
                return False
            current = current.parent

    def subtree_refusal(self, directory: Path) -> str | None:
        """整棵子树的复验结论; 同一个容器的条目共用一次遍历."""
        if directory not in self.subtrees:
            self.subtrees[directory] = _reverify_subtree(directory, orphan_scan=self.orphan_scan)
        return self.subtrees[directory]


def _host_container(path: Path, containers: dict[str, Path]) -> Path | None:
    """条目所属的容器 (残留目录) 路径; 不在任何容器下时返回 None. 由深到浅取首个命中."""
    key = PurePath(path_key(path))
    for candidate in (key, *key.parents):
        container = containers.get(os.fspath(candidate))
        if container is not None:
            return container
    return None


@in_thread
def _reverify(entry: InventoryEntry, *, probe: _MediaProbe, container: Path | None) -> str | None:
    """条目是否仍然成立; 不成立时返回原因.

    判定与扫描时同一套条件 (``orphan.py``), 数据来源从遍历换成就地读取:

    - 残留条目: 宿主容器的整棵子树复验一次 (子树里出现媒体、不可删除的子项、白名单外的文件
      都不再成立), 再加上每一级祖先的直接子项 — 祖先旁边出现媒体同样不再成立;
    - 库根与扫描范围目录的条目没有容器: 祖先链从它所在的那一层算起, 与扫描时的条件一致;
    - 空目录条目: 目录不再为空即拒绝, 执行侧删目录是递归的, 后来落进去的内容会一起没;
    - 冷静期不重复施加: 条目已经过用户确认, 再按时间否决只会让删除在无提示的情况下少做.
    """
    if entry.reason is InventoryReason.EMPTY_DIR:
        return _reverify_empty_dir(entry.path)
    if entry.reason is not InventoryReason.ORPHAN:
        return None
    if container is not None:
        refusal = probe.subtree_refusal(container)
        if refusal is not None:
            return refusal
    if probe.ancestors_have_media(container if container is not None else entry.path.parent):
        return "目录的祖先里出现了媒体"
    return None


def _reverify_empty_dir(directory: Path) -> str | None:
    try:
        with os.scandir(directory) as scanned:
            for _ in scanned:
                return "目录不再是空的"
    except OSError as exc:
        return f"目录无法读取: {exc}"
    return None


def _reverify_subtree(directory: Path, *, orphan_scan: OrphanScan) -> str | None:
    """整棵子树的复验: 与扫描时的条件同口径 (冷静期除外).

    判定顺序同样按契约: 垃圾项先于媒体判据 (``._x.mp4`` 是伴生文件, 不是视频); 垃圾项不否决,
    不可删除的目录子项否决整棵子树.
    """
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError as exc:
        return f"目录无法读取: {exc}"
    trailer = orphan_scan.trailer_matcher()
    for child in children:
        path = Path(child.path)
        try:
            child_stat = path.lstat()
        except OSError as exc:
            return f"子项无法读取: {exc}"
        is_dir = stat.S_ISDIR(child_stat.st_mode)
        junk = classify_junk(path, is_dir=is_dir)
        if is_dir:
            if junk is JunkKind.VETO:
                return f"目录里出现了不可删除的子项: {path.name}"
            refusal = _reverify_subtree(path, orphan_scan=orphan_scan)
            if refusal is not None:
                return refusal
            continue
        if junk is not None:
            continue
        if orphan_scan.is_media(path, trailer=trailer):
            return f"目录里出现了媒体: {path.name}"
        if not orphan_scan.is_companion(path):
            return f"目录里出现了无法解释的文件: {path.name}"
    return None


def _level_has_media(directory: Path, *, orphan_scan: OrphanScan) -> bool:
    """该目录的直接子项里是否有媒体; 读不到时按有媒体处理 (保守)."""
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError:
        return True
    trailer = orphan_scan.trailer_matcher()
    for child in children:
        path = Path(child.path)
        try:
            is_dir = stat.S_ISDIR(path.lstat().st_mode)
        except OSError:
            return True
        if classify_junk(path, is_dir=is_dir) is not None:
            continue
        if is_dir:
            continue
        if orphan_scan.is_media(path, trailer=trailer):
            return True
    return False
