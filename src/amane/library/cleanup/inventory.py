"""清理清单: 无效文件与空目录的快照, 以及进程内的存放.

清单由扫描产出 (规则来源) 或由选中项展开产出 (显式来源), 前端展示并确认后交给删除任务执行.
存放只在进程内: 不落库, 因此没有迁移与残留; 进程重启即丢失, 面板要求重新生成.

标识是后端生成的随机值, 不用时间戳: 时间戳可猜, 而扫描任务可以由用户随时提交.
"""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ...utils.path import path_key
from ...utils.threads import in_thread
from ..rules import TRASH_DIRNAME
from ..scan import LibraryFileKind, LibraryHit, LibraryScan, UnwantedKind
from .orphan import (
    ORPHAN_COOLDOWN_SECONDS,
    BlockedDirs,
    JunkKind,
    OrphanScan,
    classify_junk,
)

if TYPE_CHECKING:
    from re import Pattern

logger = structlog.get_logger()

# 单份清单的条目上限. 触顶只丢条目并标记已截断, 遍历照常走完: 截断只会少删, 但必须在面板上可见.
MAX_INVENTORY_ENTRIES = 20000
# 每库每来源保留的清单份数. 固定窗口, 因此存放与任务表无关.
INVENTORY_RETENTION = 4
# 清单有效期, 自生成时刻起算, 读取不续期.
INVENTORY_TTL_SECONDS = 24 * 3600


class InventorySource(StrEnum):
    """清单来源分组. 面板只渲染规则来源的最新一份; 回收目录与选中项展开发往各自的分组, 互不挤占保留窗口."""

    RULES = "rules"
    EXPLICIT = "explicit"
    TRASH = "trash"


class InventoryReason(StrEnum):
    BLACKLIST = "blacklist"
    UNDERSIZED = "undersized"
    EMPTY_DIR = "empty_dir"
    EXPLICIT = "explicit"
    ORPHAN = "orphan"
    """只含附属文件的目录: 视频被搬走或删除后留下 NFO / 图片 / 字幕, 整目录登记为一条."""


class InventoryEntryKind(StrEnum):
    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"


_REASONS: dict[UnwantedKind, InventoryReason] = {
    UnwantedKind.BLACKLIST: InventoryReason.BLACKLIST,
    UnwantedKind.UNDERSIZED: InventoryReason.UNDERSIZED,
}


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    path: Path
    kind: InventoryEntryKind
    reason: InventoryReason
    size: int | None = None
    dev: int | None = None
    ino: int | None = None
    nlink: int | None = None
    expandable: bool = False
    """容器条目: 执行时展开为它的子条目, 自身不作为删除目标.

    残留目录属于这一种 — 用户要能逐个文件选择删或不删, 因此内容本身就是条目; 目录条目
    只承载「这是一处残留」与整目录的汇总, 删除时跳过它, 删完由剪枝回收空目录.
    """
    noise: bool = False
    """操作系统与同步工具的产物 (.DS_Store、Thumbs.db、*.tmp 等).

    它们同样会被删除, 因此也要登记为条目; 面板默认折叠这一类, 由用户展开核对.
    """


@dataclass(frozen=True, slots=True)
class DirCoverage:
    """目录的磁盘子项总数, 以及清单全部条目被删除后该目录是否会空.

    ``will_be_empty`` 自底向上算出: 子目录会空也算作「会消失的子项」, 与剪枝的实际行为一致.
    读不到的目录不登记覆盖, 面板因此不会预告它将被清除.
    """

    disk_children: int
    will_be_empty: bool


@dataclass
class CleanupInventory:
    """一份清单. ``root`` 是生成时的库根, 执行前必须与当前库根一致."""

    inventory_id: str
    library_id: int
    root: Path
    scope_path: Path | None
    recursive: bool
    patterns: tuple[str, ...]
    source: InventorySource
    created_at: datetime
    entries: list[InventoryEntry] = field(default_factory=list)
    """执行集合: `DELETE` 的目标只来自这里, 容器条目在展开后不在其中."""
    dirs: dict[Path, DirCoverage] = field(default_factory=dict)
    truncated: bool = False
    dropped: int = 0
    """触顶后未纳入清单的候选数; 截断时才有意义."""
    skipped_dirs: int = 0
    skipped_files: int = 0
    blocked: BlockedDirs = field(default_factory=BlockedDirs)
    """判定为候选但没有登记的目录数, 按原因分组; 只在带残留判定的扫描里累加."""
    executed: bool = False
    """已被执行任务取用: 预检一过就置位, 因此取消与中途失败同样算, 面板随即当它不存在."""
    media_hits: list[LibraryHit] = field(default_factory=list)
    """同一趟遍历命中的媒体文件; 只有 `collect_media` 时填充 (入库扫描用)."""
    tree: InventoryNode | None = field(default=None, repr=False, compare=False)

    @property
    def scoped(self) -> bool:
        """是否只覆盖库的子目录. 面板据此标注范围, 不宣称整库."""
        return self.scope_path is not None

    @property
    def total_size(self) -> int:
        """条目大小合计. 同一 inode 只算一次 (硬链接整理会产生多个名字).

        身份不可用 (拿不到设备或文件编号, 含平台给出的 0) 时不去重: 宁可把同一份数据多算几次,
        也不能把不同文件当成同一个而少算.
        """
        sizes: dict[tuple[int, int], int] = {}
        loose = 0
        for entry in self.entries:
            if entry.size is None:
                continue
            if not entry.dev or not entry.ino:
                loose += entry.size
                continue
            sizes.setdefault((entry.dev, entry.ino), entry.size)
        return loose + sum(sizes.values())


def new_inventory_id() -> str:
    return secrets.token_urlsafe(16)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InventoryStore:
    """进程内的清单存放: 每库每来源保留最近若干份, 过期即视为不存在.

    已执行的清单是花掉的: 面板侧的读取一律当它不存在, 否则用户会看到一份按设计无法再执行的清单.
    `get` 例外 — 执行侧要凭它区分「不存在」与「已执行过」, 因此过滤在调用方.
    """

    keep: int = INVENTORY_RETENTION
    ttl_seconds: int = INVENTORY_TTL_SECONDS
    now: Callable[[], datetime] = _utcnow
    _inventories: dict[tuple[int, InventorySource], list[CleanupInventory]] = field(default_factory=dict)

    def put(self, inventory: CleanupInventory) -> None:
        bucket = self._inventories.setdefault((inventory.library_id, inventory.source), [])
        bucket.append(inventory)
        if len(bucket) > self.keep:
            del bucket[: len(bucket) - self.keep]

    def latest(
        self, library_id: int, source: InventorySource, *, max_age: timedelta | None = None
    ) -> CleanupInventory | None:
        """最新的可用清单; ``max_age`` 用于只接受刚产出过的那一份."""
        within = max_age.total_seconds() if max_age is not None else None
        for inventory in reversed(self._inventories.get((library_id, source), [])):
            if not inventory.executed and not self._expired(inventory, within=within):
                return inventory
        return None

    def get(self, inventory_id: str) -> CleanupInventory | None:
        """按标识查找; 不存在与已过期同样返回 None, 调用方不做区分."""
        for bucket in self._inventories.values():
            for inventory in bucket:
                if inventory.inventory_id == inventory_id:
                    return None if self._expired(inventory) else inventory
        return None

    def drop_library(self, library_id: int) -> None:
        """库路径被修改或库被删除时丢弃该库清单."""
        for key in [key for key in self._inventories if key[0] == library_id]:
            del self._inventories[key]

    def _expired(self, inventory: CleanupInventory, *, within: float | None = None) -> bool:
        """``within`` 覆盖有效期 (秒); 缺省用清单本身的 TTL."""
        return self.now() - inventory.created_at > timedelta(seconds=self.ttl_seconds if within is None else within)


@dataclass
class _ScanState:
    entries: list[InventoryEntry]
    dirs: dict[Path, DirCoverage]
    limit: int
    collect_media: bool = False
    media: list[LibraryHit] = field(default_factory=list)
    truncated: bool = False
    dropped: int = 0
    skipped_dirs: int = 0
    skipped_files: int = 0
    blocked: BlockedDirs = field(default_factory=BlockedDirs)
    orphan_scan: OrphanScan | None = None
    """带残留判定时非空; 缺省不判定, 与既有调用方兼容."""
    now: float = 0.0
    """一次扫描只取一个时刻: 否则同一份清单里两个目录会按不同时刻判定冷却期."""
    trailer_matcher: Pattern[str] | None = None
    """预告片正则只编译一次: 判定在万级子项上反复调用."""

    @property
    def full(self) -> bool:
        return len(self.entries) >= self.limit


@dataclass(frozen=True, slots=True)
class _LevelFacts:
    """本目录的**直接子项**的判定输入.

    子目录的「祖先直接子项里有没有媒体」由它传递; 库根与扫描范围目录的逐文件登记也只看它 —
    整库别处有正片与这一层的文件是不是残留无关.
    """

    has_media: bool = False
    """本层有媒体; 读不到的子项按有媒体处理 (保守)."""
    veto: bool = False
    """本层有不可删除的**文件**子项 (下载进度). 目录类不可删除项不影响逐文件登记: 整层不会被删."""
    newest_mtime: float = 0.0
    companions: tuple[tuple[Path, os.stat_result], ...] = ()
    """本层的附属文件."""


@dataclass(frozen=True, slots=True)
class _DirResult:
    """一个目录的统计.

    ``entries`` / ``disappears`` 供覆盖表使用; 其余是残留判定的输入, 由递归向上汇总 —
    它们都在同一次遍历里算出, 判定因此不额外访问磁盘.
    """

    children: int
    entries: int
    disappears: bool
    level: _LevelFacts = field(default_factory=_LevelFacts)
    subtree_has_media: bool = False
    """子树里是否有媒体."""
    explainable: bool = True
    """子树里是否只有附属文件与可随目录删除的系统产物."""
    undeletable: bool = False
    """子树里是否有不可删除的子项 (回收目录、版本库、下载进度等)."""
    has_content: bool = False
    """子树里是否有文件; 只有空目录时为假 (那种情况由空目录条目处置)."""
    orphan_in_subtree: bool = False
    """子树里是否已经登记了残留条目; 外层候选命中时由它决定要不要吸收子层条目."""
    blocked_in_subtree: bool = False
    """子树里是否已有未登记的候选; 由它保证同一原因只按最外层那一个计数."""
    newest_mtime: float = 0.0
    """子树内最新的 mtime; 残留判定的冷却期按它算, 深层刚动过同样拦住整棵子树."""
    bytes_total: int = 0
    """子树内会被删除的字节合计, 写进目录条目的大小."""
    companions: tuple[tuple[Path, os.stat_result], ...] = ()
    """子树内的附属文件; 目录命中残留时登记为条目, 让面板列出即将删除的内容."""
    noise: tuple[tuple[Path, os.stat_result], ...] = ()
    """子树内的系统产物; 与附属文件同样登记为条目, 登记时标 `InventoryEntry.noise`."""


@in_thread
def scan_inventory(
    scope_dir: Path,
    *,
    library_id: int,
    library_root: Path,
    recursive: bool,
    patterns: Sequence[str],
    scan: LibraryScan,
    source: InventorySource = InventorySource.RULES,
    limit: int = MAX_INVENTORY_ENTRIES,
    collect_media: bool = False,
    orphan_scan: OrphanScan | None = None,
    now: float | None = None,
) -> CleanupInventory:
    """遍历范围内的一层或整棵子树, 产出清单.

    与入库扫描共用同一趟遍历: `collect_media` 为真时同时收集媒体命中, 不额外遍历磁盘.

    - 文件黑名单与小于最小视频大小的文件是条目; 预告片与其余文件不是.
    - 磁盘上没有子项的目录作为「扫描时已空」的条目.
    - 回收目录子树整棵不进入清单, 且算作不可删除的子项: 其父目录不会因此被预告清除.
    - 读不到的目录与 stat 失败的文件只计数, 其余子项继续.
    - 达到条目上限后不再登记条目, 但遍历照常走完 (媒体命中必须完整), 并记下未纳入的候选数.
    - `orphan_scan` 非空时额外把「只含附属文件的目录」登记为一条目录条目, 见 `orphan.py`.
    """
    try:
        scope_mtime = scope_dir.lstat().st_mtime
    except OSError:
        scope_mtime = 0.0
    state = _ScanState(
        entries=[],
        dirs={},
        limit=limit,
        collect_media=collect_media,
        orphan_scan=orphan_scan,
        now=now if now is not None else _utcnow().timestamp(),
        trailer_matcher=orphan_scan.trailer_matcher() if orphan_scan is not None else None,
    )
    result = _walk(scope_dir, state=state, scan=scan, recursive=recursive, ancestor_has_media=False)
    if result is not None and orphan_scan is not None:
        _register_scope_files(result=result, state=state, directory_mtime=scope_mtime)
    inventory = CleanupInventory(
        inventory_id=new_inventory_id(),
        library_id=library_id,
        root=library_root,
        scope_path=None if scope_dir == library_root else scope_dir,
        recursive=recursive,
        patterns=tuple(patterns),
        source=source,
        created_at=datetime.now(UTC),
        entries=state.entries,
        dirs=state.dirs,
        truncated=state.truncated,
        dropped=state.dropped,
        skipped_dirs=state.skipped_dirs,
        skipped_files=state.skipped_files,
        blocked=state.blocked,
        media_hits=state.media,
    )
    logger.info(
        "inventory scanned",
        library_id=library_id,
        scope=str(scope_dir),
        entries=len(inventory.entries),
        dirs=len(inventory.dirs),
        truncated=inventory.truncated,
        dropped=inventory.dropped,
        skipped_dirs=inventory.skipped_dirs,
        skipped_files=inventory.skipped_files,
        blocked_dirs=inventory.blocked.total,
    )
    return inventory


def _walk(
    directory: Path,
    *,
    state: _ScanState,
    scan: LibraryScan,
    recursive: bool,
    ancestor_has_media: bool,
) -> _DirResult | None:
    """登记 ``directory`` 下的条目与覆盖信息; 读不到该目录时返回 None.

    触顶后不提前返回: 本趟遍历同时承担入库扫描的媒体收集, 半途而废会让媒体列表缺项,
    ``REFRESH`` 的增删据此判断存在性, 缺项即误删索引. 触顶只丢条目与覆盖信息.
    """
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError as exc:
        state.skipped_dirs += 1
        logger.warning("inventory scan directory unreadable", path=str(directory), error=str(exc))
        return None

    total = 0
    removed = 0
    entries = 0
    level_media = False
    level_veto = False
    level_newest_mtime = 0.0
    subtree_media = False
    explainable = True
    undeletable = False
    has_content = False
    orphan_in_subtree = False
    blocked_in_subtree = False
    newest_mtime = 0.0
    bytes_total = 0
    subdirs: list[tuple[Path, os.stat_result]] = []
    companions: list[tuple[Path, os.stat_result]] = []
    noise: list[tuple[Path, os.stat_result]] = []
    # 分两趟: 先确定本层有没有媒体, 再递归子目录. 本层媒体是子目录的「祖先直接子项含媒体」,
    # 边遍历边传递会让排在媒体之前的子目录漏掉这个条件 (目录枚举顺序不保证). 本层媒体与
    # 最新 mtime 在第一个循环里一并算出, 不为此重扫子项.
    for child in children:
        total += 1
        path = Path(child.path)
        if child.name == TRASH_DIRNAME:
            # 回收目录: 不进清单, 也不计作会消失的子项.
            undeletable = True
            continue
        try:
            child_stat = path.lstat()
        except OSError as exc:
            state.skipped_files += 1
            logger.warning("inventory scan entry unreadable", path=str(path), error=str(exc))
            # 读不到的按有媒体处理 (保守), 与逐层判定同一口径.
            level_media = True
            continue
        level_newest_mtime = max(level_newest_mtime, child_stat.st_mtime)
        newest_mtime = max(newest_mtime, child_stat.st_mtime)
        is_dir = stat.S_ISDIR(child_stat.st_mode)
        # 判定顺序是契约的一部分: 系统产物先于媒体判据 (._x.mp4 是伴生文件, 不是视频),
        # 媒体先于白名单 (用户可以把 .mp4 写进 subtitle_extensions).
        junk = classify_junk(path, is_dir=is_dir)
        if is_dir:
            if junk is JunkKind.VETO:
                # 不可删除的子项: 不递归、不登记, 并使该目录不判定. 口径与回收目录一致,
                # 差别只在它不需要单独列出 (回收目录要在面板上有名字).
                undeletable = True
                continue
            subdirs.append((path, child_stat))
            continue
        has_content = True
        if _process_file(
            path,
            state=state,
            scan=scan,
            child_stat=child_stat,
            is_symlink=stat.S_ISLNK(child_stat.st_mode),
        ):
            entries += 1
            removed += 1
            bytes_total += child_stat.st_size
            if junk is None:
                level_media = level_media or _is_media(path, state=state)
            continue
        if junk is JunkKind.DELETABLE:
            noise.append((path, child_stat))
            continue
        if junk is JunkKind.VETO:
            undeletable = True
            level_veto = True
            continue
        orphan_scan = state.orphan_scan
        if orphan_scan is not None and orphan_scan.is_media(path, trailer=state.trailer_matcher):
            level_media = True
            continue
        if orphan_scan is not None and orphan_scan.is_companion(path):
            # 附属文件: 判定通过, 不进执行集合. 目录命中残留时由 `_register_orphan` 登记为
            # 条目, 供面板列出「即将删除什么」; 库根与扫描范围目录那两层另行逐文件登记.
            bytes_total += child_stat.st_size
            companions.append((path, child_stat))
            continue
        explainable = False

    level = _LevelFacts(
        has_media=level_media,
        veto=level_veto,
        newest_mtime=level_newest_mtime,
        companions=tuple(companions),
    )
    ancestor_has_media = ancestor_has_media or level_media
    for path, child_stat in subdirs:
        sub = _record_dir(
            path,
            state=state,
            scan=scan,
            recursive=recursive,
            ancestor_has_media=ancestor_has_media,
            directory_mtime=child_stat.st_mtime,
        )
        if sub is None:
            continue
        entries += sub.entries
        if sub.disappears:
            removed += 1
        subtree_media = subtree_media or sub.subtree_has_media
        explainable = explainable and sub.explainable
        undeletable = undeletable or sub.undeletable
        orphan_in_subtree = orphan_in_subtree or sub.orphan_in_subtree
        blocked_in_subtree = blocked_in_subtree or sub.blocked_in_subtree
        has_content = has_content or sub.has_content
        newest_mtime = max(newest_mtime, sub.newest_mtime)
        bytes_total += sub.bytes_total
        # 子目录被外层吸收时它的附属文件要跟着上浮, 否则嵌套残留的内容列不出来.
        companions.extend(sub.companions)
        noise.extend(sub.noise)

    return _DirResult(
        children=total,
        entries=entries,
        disappears=total > 0 and removed == total,
        level=level,
        subtree_has_media=level_media or subtree_media,
        explainable=explainable,
        undeletable=undeletable,
        has_content=has_content,
        orphan_in_subtree=orphan_in_subtree,
        blocked_in_subtree=blocked_in_subtree,
        newest_mtime=newest_mtime,
        bytes_total=bytes_total,
        companions=tuple(companions),
        noise=tuple(noise),
    )


def _is_media(path: Path, *, state: _ScanState) -> bool:
    """本层是否存在媒体. 只在带残留判定的扫描里查询, 其余调用方不承担这次判定."""
    orphan_scan = state.orphan_scan
    if orphan_scan is None:
        return False
    return orphan_scan.is_media(path, trailer=state.trailer_matcher)


def _process_file(
    path: Path,
    *,
    state: _ScanState,
    scan: LibraryScan,
    child_stat: os.stat_result,
    is_symlink: bool,
) -> bool:
    """登记无效文件条目; 返回该文件是否进清单."""
    kind = scan.classify(path)
    if kind is LibraryFileKind.MEDIA:
        if state.collect_media:
            state.media.append(LibraryHit(path, kind))
        return False
    if kind is not LibraryFileKind.UNWANTED:
        return False
    entry = _file_entry(path, scan=scan, child_stat=child_stat, is_symlink=is_symlink)
    return entry is not None and _record_entry(entry, state=state)


def _record_dir(
    path: Path,
    *,
    state: _ScanState,
    scan: LibraryScan,
    recursive: bool,
    ancestor_has_media: bool,
    directory_mtime: float,
) -> _DirResult | None:
    """登记子目录; 返回该子目录的统计 (空目录与残留目录在此转成条目), 不处置时返回 None."""
    if not recursive:
        # 不递归时子目录不是处置对象: 计入子项数, 使父目录不会被预告清除.
        return None
    entries_before = len(state.entries)
    dirs_before = set(state.dirs)
    blocked_below = False
    result = _walk(path, state=state, scan=scan, recursive=recursive, ancestor_has_media=ancestor_has_media)
    if result is None:
        return None
    if result.children == 0:
        # 空目录先于残留判定: 它同样满足「子树里没有媒体且内容可解释」, 但它是一条空目录
        # 条目, 不是残留目录.
        entry = InventoryEntry(path=path, kind=InventoryEntryKind.DIR, reason=InventoryReason.EMPTY_DIR)
        if not _record_entry(entry, state=state):
            return None
        return _DirResult(children=0, entries=1, disappears=True)
    orphan_scan = state.orphan_scan
    if orphan_scan is not None:
        verdict = orphan_scan.verdict(
            path,
            ancestor_has_media=ancestor_has_media,
            subtree_has_media=result.subtree_has_media,
            subtree_explainable=result.explainable,
            subtree_undeletable=result.undeletable,
            subtree_has_content=result.has_content,
            newest_mtime=result.newest_mtime,
            directory_mtime=directory_mtime,
            now=state.now,
        )
        if verdict is not None and verdict.registrable:
            absorbed = _register_orphan(
                path,
                result=result,
                state=state,
                entries_before=entries_before,
                dirs_before=dirs_before,
            )
            # 覆盖信息照常登记: 面板要预告的是「这条目录条目删掉之后父目录会空」.
            _record_coverage(path, result=absorbed, state=state)
            return absorbed
        # 不是候选 (目录里有媒体或不可删除的子项) 时落到下面照常登记覆盖信息, 并继续空目录判定.
        # 只按最外层那一个计数: 后代已登记或被否决时, 同一原因不该在祖先上再计一次.
        if verdict is not None and verdict.blocked is not None:
            if not result.orphan_in_subtree and not result.blocked_in_subtree:
                state.blocked = state.blocked.plus(verdict.blocked)
            blocked_below = True
    _record_coverage(path, result=result, state=state)
    if blocked_below:
        return replace(result, blocked_in_subtree=True)
    return result


def _record_coverage(path: Path, *, result: _DirResult, state: _ScanState) -> None:
    """登记目录的覆盖信息.

    截断后不登记: 「将变空」只在整份清单完整时才有意义. 只登记子树里有条目的目录 —
    「将变空」只对它们有意义, 也限制覆盖表的规模.
    """
    if state.truncated or result.entries == 0:
        return
    state.dirs[path] = DirCoverage(disk_children=result.children, will_be_empty=result.disappears)


def _register_orphan(
    path: Path,
    *,
    result: _DirResult,
    state: _ScanState,
    entries_before: int,
    dirs_before: set[Path],
) -> _DirResult:
    """把目录登记为一条容器条目, 并把子树里的内容登记为可选择的条目.

    内容本身就是条目, 用户因此能逐个文件选择删或不删, 与文件黑名单 / 小于最小视频大小的条目一致; 目录
    条目只承载「这是一处残留」与整目录的汇总, 执行时跳过它 (删完由剪枝回收空目录).

    子树里已经登记的条目 (空子目录、命中文件黑名单的文件) 同样保留: 它们也是这一处残留的内容,
    保留条目才能使面板列出并删除这些内容. 只有会重新登记的那部分 (附属文件与系统产物, 含嵌套
    容器上浮上来的) 摘掉重登, 以免同一路径出现两条; 嵌套的容器条目一并摘掉, 它的内容已经上浮.

    触顶时容器条目先占住一个位置: 少了它, 面板上就没有这处残留的入口; 内容登记完再挪到末尾,
    因此先被丢弃的是内容, 容器不受影响.
    """
    container = InventoryEntry(
        path=path,
        kind=InventoryEntryKind.DIR,
        reason=InventoryReason.ORPHAN,
        size=result.bytes_total,
        expandable=True,
    )
    if not _record_entry(container, state=state):
        return result
    # `entries_before` 取自进入子树之前, 末尾是刚登记的容器: 这一段都是被吸收的子树.
    inner = state.entries[entries_before:-1]
    relisted = {path_key(item[0]) for item in (*result.companions, *result.noise)}
    kept = [entry for entry in inner if not entry.expandable and path_key(entry.path) not in relisted]
    state.entries[entries_before:-1] = kept
    # 内容登记为真正的条目; 系统产物标 noise, 面板默认折叠这一类.
    listed = [
        *((item[0], item[1], False) for item in result.companions),
        *((item[0], item[1], True) for item in result.noise),
    ]
    for listed_path, listed_stat, is_noise in listed:
        _record_entry(
            InventoryEntry(
                path=listed_path,
                kind=InventoryEntryKind.SYMLINK if stat.S_ISLNK(listed_stat.st_mode) else InventoryEntryKind.FILE,
                reason=InventoryReason.ORPHAN,
                size=listed_stat.st_size,
                dev=listed_stat.st_dev,
                ino=listed_stat.st_ino,
                nlink=listed_stat.st_nlink,
                noise=is_noise,
            ),
            state=state,
        )
    # 容器条目此刻正好在保留条目的后面: 按下标取而不是按值取, 否则挪走的是保留下来的第一条.
    state.entries.append(state.entries.pop(entries_before + len(kept)))
    for covered in [key for key in state.dirs if key not in dirs_before]:
        del state.dirs[covered]
    return _DirResult(
        children=result.children,
        entries=1 + len(listed) + len(kept),
        disappears=result.disappears,
        level=result.level,
        subtree_has_media=result.subtree_has_media,
        explainable=result.explainable,
        undeletable=result.undeletable,
        orphan_in_subtree=True,
        blocked_in_subtree=result.blocked_in_subtree,
        newest_mtime=result.newest_mtime,
        bytes_total=result.bytes_total,
        companions=result.companions,
        noise=result.noise,
    )


def _register_scope_files(
    *,
    result: _DirResult,
    state: _ScanState,
    directory_mtime: float,
) -> None:
    """库根与扫描范围目录的退化处置: 逐文件登记本层的附属文件.

    这两层永不登记为目录条目 (一条条目就能带走整库内容), 但「下载目录本身就是库根、视频
    整理走后根目录里留下 NFO 与图片」是最常见的形态, 不能整层放弃.

    条件只按**本层**算, 与目录条目的子树口径不同: 库内别处有正片, 与这一层留下的文件是不是
    残留无关, 子树口径会让上面这个形态永不生效. 本层有媒体 (正片就在旁边)、本层有下载进度
    (不可删除的文件子项)、本层刚变动过 (附属文件先落盘), 三种情况都不登记. 目录类的不可删除
    子项不影响: 这一层不会被整层删除, 回收目录与版本库目录不受牵连.
    """
    orphan_scan = state.orphan_scan
    level = result.level
    if orphan_scan is None or level.has_media or level.veto:
        return
    if state.now - max(level.newest_mtime, directory_mtime) <= ORPHAN_COOLDOWN_SECONDS:
        return
    for path, child_stat in level.companions:
        entry = InventoryEntry(
            path=path,
            kind=InventoryEntryKind.SYMLINK if stat.S_ISLNK(child_stat.st_mode) else InventoryEntryKind.FILE,
            reason=InventoryReason.ORPHAN,
            size=child_stat.st_size,
            dev=child_stat.st_dev,
            ino=child_stat.st_ino,
            nlink=child_stat.st_nlink,
        )
        _record_entry(entry, state=state)


def _file_entry(
    path: Path, *, scan: LibraryScan, child_stat: os.stat_result, is_symlink: bool
) -> InventoryEntry | None:
    kind = scan.unwanted_kind(path)
    if kind is None:
        return None
    return InventoryEntry(
        path=path,
        kind=InventoryEntryKind.SYMLINK if is_symlink else InventoryEntryKind.FILE,
        reason=_REASONS[kind],
        size=child_stat.st_size,
        dev=child_stat.st_dev,
        ino=child_stat.st_ino,
        nlink=child_stat.st_nlink,
    )


def _record_entry(entry: InventoryEntry, *, state: _ScanState) -> bool:
    if state.full:
        state.truncated = True
        state.dropped += 1
        return False
    state.entries.append(entry)
    return True


@dataclass(slots=True)
class InventoryNode:
    """面板的树节点. 目录节点只出现在条目路径的祖先链上, 因此树不是库目录树的副本."""

    path: Path
    name: str
    is_dir: bool
    is_symlink: bool
    reason: InventoryReason | None
    size: int | None
    hardlink: bool
    entry_count: int
    entry_bytes: int
    will_be_empty: bool
    expandable: bool = False
    """容器条目: 执行时展开为子条目; 面板把它当目录节点渲染."""
    noise: bool = False
    """取自 `InventoryEntry.noise`."""
    children: tuple[InventoryNode, ...] = ()
    """容器目录的子节点是清单条目与分支."""


def build_inventory_tree(inventory: CleanupInventory) -> InventoryNode:
    """把清单折叠成树. 同 inode 只算一次大小, 与 `total_size` 一致.

    容器条目覆盖的路径由条目节点代表, 不再另建分支节点, 否则同一路径会同时产出条目节点与
    分支节点, `find_inventory_node` 会返回没有子节点的那个, 面板无法展开.
    """
    by_parent: dict[Path, list[InventoryEntry]] = {}
    subdirs: dict[Path, set[Path]] = {}
    counted: set[Path] = set()
    seen_inodes: set[tuple[int, int]] = set()
    for entry in inventory.entries:
        # 身份不可用时每个名字各算一次, 与 `total_size` 一致.
        if entry.dev and entry.ino:
            key = (entry.dev, entry.ino)
            if key not in seen_inodes:
                seen_inodes.add(key)
                counted.add(entry.path)
        else:
            counted.add(entry.path)
        parent = entry.path.parent
        by_parent.setdefault(parent, []).append(entry)
        cursor = parent
        while cursor != inventory.root and _is_under(cursor, inventory.root):
            subdirs.setdefault(cursor.parent, set()).add(cursor)
            cursor = cursor.parent

    # 容器条目覆盖的路径: 这些路径由条目节点代表, 不再另建分支节点, 否则同一路径会出现两个节点.
    hosts = {entry.path for entry in inventory.entries if entry.expandable}

    def build(directory: Path) -> InventoryNode:
        nodes = [_entry_node(entry, counted=counted) for entry in by_parent.get(directory, [])]
        for child_dir in sorted(subdirs.get(directory, ()), key=lambda p: p.name):
            if child_dir in hosts:
                # 把它的内容挂到容器上, 容器节点由 `nodes` 里那条条目节点充当.
                container = next(node for node in nodes if node.path == child_dir)
                container.children = build(child_dir).children
                continue
            nodes.append(build(child_dir))
        nodes.sort(key=lambda node: (not node.is_dir, node.name))
        # 容器条目的汇总取子项: 它自身不是删除目标, 面板按子项算选中量.
        for node in nodes:
            if node.expandable:
                node.entry_count = sum(child.entry_count for child in node.children)
                node.entry_bytes = sum(child.entry_bytes for child in node.children)
        coverage = inventory.dirs.get(directory)
        # 容器的子项挂在容器自己下面, 不在这一层: 父级按容器的汇总值计一次.
        return InventoryNode(
            path=directory,
            name=directory.name,
            is_dir=True,
            is_symlink=False,
            reason=None,
            size=None,
            hardlink=False,
            entry_count=sum(node.entry_count for node in nodes),
            entry_bytes=sum(node.entry_bytes for node in nodes),
            will_be_empty=coverage.will_be_empty if coverage is not None else False,
            children=tuple(nodes),
        )

    return build(inventory.root)


def inventory_tree(inventory: CleanupInventory) -> InventoryNode:
    """面板读取用的树. 构建是 O(清单条目数), 而清单生成后内容不再变化, 因此只构建一次.

    面板翻页会反复读取同一份清单, 每页都重建对两万条候选是纯浪费.
    """
    if inventory.tree is None:
        inventory.tree = build_inventory_tree(inventory)
    return inventory.tree


def _entry_node(entry: InventoryEntry, *, counted: set[Path]) -> InventoryNode:
    is_dir = entry.kind is InventoryEntryKind.DIR
    return InventoryNode(
        path=entry.path,
        name=entry.path.name,
        is_dir=is_dir,
        is_symlink=entry.kind is InventoryEntryKind.SYMLINK,
        reason=entry.reason,
        size=entry.size,
        hardlink=(entry.nlink or 1) > 1,
        entry_count=1,
        entry_bytes=entry.size if entry.path in counted and entry.size is not None else 0,
        will_be_empty=is_dir,
        expandable=entry.expandable,
        noise=entry.noise,
    )


def find_inventory_node(root: InventoryNode, path: Path) -> InventoryNode | None:
    """按绝对路径在树里查找节点 (面板按目录下钻)."""
    if root.path == path:
        return root
    for child in root.children:
        if child.path == path:
            return child
        if child.is_dir and _is_under(path, child.path):
            return find_inventory_node(child, path)
    return None


def _is_under(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


@in_thread
def scan_trash(
    trash_dir: Path,
    *,
    library_id: int,
    library_root: Path,
    limit: int = MAX_INVENTORY_ENTRIES,
) -> CleanupInventory:
    """把回收目录的历史内容展开成清单.

    不做规则判定: 其下每个文件都是条目, 空目录同样是条目. 回收目录自身从不作为条目.
    """
    state = _ScanState(entries=[], dirs={}, limit=limit)
    _walk_explicit(trash_dir, state=state)
    inventory = CleanupInventory(
        inventory_id=new_inventory_id(),
        library_id=library_id,
        root=library_root,
        scope_path=trash_dir,
        recursive=True,
        patterns=(),
        source=InventorySource.TRASH,
        created_at=datetime.now(UTC),
        entries=state.entries,
        dirs=state.dirs,
        truncated=state.truncated,
        dropped=state.dropped,
        skipped_dirs=state.skipped_dirs,
        skipped_files=state.skipped_files,
    )
    logger.info(
        "trash scanned",
        library_id=library_id,
        entries=len(inventory.entries),
        truncated=inventory.truncated,
        dropped=inventory.dropped,
        skipped_dirs=inventory.skipped_dirs,
        skipped_files=inventory.skipped_files,
    )
    return inventory


def _walk_explicit(directory: Path, *, state: _ScanState) -> _DirResult | None:
    """登记回收目录下的全部内容; 读不到该目录时返回 None.

    与规则来源同一条规则: 触顶只丢条目与覆盖信息, 遍历照常走完.
    """
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError as exc:
        state.skipped_dirs += 1
        logger.warning("trash scan directory unreadable", path=str(directory), error=str(exc))
        return None

    total = 0
    removed = 0
    entries = 0
    for child in children:
        total += 1
        path = Path(child.path)
        try:
            child_stat = path.lstat()
        except OSError as exc:
            state.skipped_files += 1
            logger.warning("trash scan entry unreadable", path=str(path), error=str(exc))
            continue
        if stat.S_ISDIR(child_stat.st_mode):
            sub = _walk_explicit(path, state=state)
            if sub is None:
                continue
            if sub.children == 0:
                entry = InventoryEntry(path=path, kind=InventoryEntryKind.DIR, reason=InventoryReason.EXPLICIT)
                if _record_entry(entry, state=state):
                    entries += 1
                    removed += 1
                continue
            if not state.truncated:
                state.dirs[path] = DirCoverage(disk_children=sub.children, will_be_empty=sub.disappears)
            entries += sub.entries
            if sub.disappears:
                removed += 1
            continue
        entry = InventoryEntry(
            path=path,
            kind=InventoryEntryKind.SYMLINK if stat.S_ISLNK(child_stat.st_mode) else InventoryEntryKind.FILE,
            reason=InventoryReason.EXPLICIT,
            size=child_stat.st_size,
            dev=child_stat.st_dev,
            ino=child_stat.st_ino,
            nlink=child_stat.st_nlink,
        )
        if _record_entry(entry, state=state):
            entries += 1
            removed += 1

    return _DirResult(children=total, entries=entries, disappears=total > 0 and removed == total)
