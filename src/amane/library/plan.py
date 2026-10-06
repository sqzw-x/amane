"""清理清单: 无效文件与已空目录的快照, 以及进程内的存放.

清单由扫描产出 (规则来源) 或由选中项展开产出 (显式来源), 前端展示并确认后交给删除任务执行.
存放只在进程内: 不落库, 因此没有迁移与残留; 进程重启即丢失, 面板要求重新生成.

标识是后端生成的随机值, 不用时间戳: 时间戳可猜, 而扫描任务可以由用户随时提交.
"""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import structlog

from ..utils.threads import in_thread
from .rules import TRASH_DIRNAME
from .scan import LibraryScan, UnwantedKind

logger = structlog.get_logger()

# 单份清单的条目上限. 触顶即停止遍历并标记已截断: 截断只会少删, 但必须在面板上可见.
MAX_PLAN_ENTRIES = 20000
# 每库每来源保留的清单份数. 固定窗口, 因此存放与任务表无关.
PLAN_RETENTION = 4
# 清单有效期, 自生成时刻起算, 读取不续期.
PLAN_TTL_SECONDS = 24 * 3600


class PlanSource(StrEnum):
    """清单来源分组. 面板只渲染规则来源的最新一份."""

    RULES = "rules"
    EXPLICIT = "explicit"


class PlanReason(StrEnum):
    BLACKLIST = "blacklist"
    UNDERSIZED = "undersized"
    EMPTY_DIR = "empty_dir"
    EXPLICIT = "explicit"


class PlanEntryKind(StrEnum):
    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"


_REASONS: dict[UnwantedKind, PlanReason] = {
    UnwantedKind.BLACKLIST: PlanReason.BLACKLIST,
    UnwantedKind.UNDERSIZED: PlanReason.UNDERSIZED,
}


@dataclass(frozen=True, slots=True)
class PlanEntry:
    path: Path
    kind: PlanEntryKind
    reason: PlanReason
    size: int | None = None
    outside: bool = False
    dev: int | None = None
    ino: int | None = None
    nlink: int | None = None


@dataclass(frozen=True, slots=True)
class DirCoverage:
    """目录的磁盘子项总数, 以及清单全部条目被删除后该目录是否会空.

    ``will_be_empty`` 自底向上算出: 子目录会空也算作「会消失的子项」, 与剪枝的实际行为一致.
    读不到的目录不登记覆盖, 面板因此不会预告它将被清除.
    """

    disk_children: int
    will_be_empty: bool


@dataclass
class LibraryPlan:
    """一份清单. ``root`` 是生成时的库根, 执行前必须与当前库根一致."""

    plan_id: str
    library_id: int
    root: Path
    scope_path: Path | None
    recursive: bool
    patterns: tuple[str, ...]
    source: PlanSource
    created_at: datetime
    entries: list[PlanEntry] = field(default_factory=list)
    dirs: dict[Path, DirCoverage] = field(default_factory=dict)
    truncated: bool = False
    skipped_dirs: int = 0
    skipped_files: int = 0
    executed: bool = False

    @property
    def scoped(self) -> bool:
        """是否只覆盖库的子目录. 面板据此标注范围, 不宣称整库."""
        return self.scope_path is not None

    @property
    def total_size(self) -> int:
        """条目体积合计. 同一 inode 只算一次 (硬链接整理会产生多个名字)."""
        sizes: dict[tuple[int, int], int] = {}
        loose = 0
        for entry in self.entries:
            if entry.size is None:
                continue
            if entry.dev is None or entry.ino is None:
                loose += entry.size
                continue
            sizes.setdefault((entry.dev, entry.ino), entry.size)
        return loose + sum(sizes.values())


def new_plan_id() -> str:
    return secrets.token_urlsafe(16)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class PlanStore:
    """进程内的清单存放: 每库每来源保留最近若干份, 过期即视为不存在."""

    keep: int = PLAN_RETENTION
    ttl_seconds: int = PLAN_TTL_SECONDS
    now: Callable[[], datetime] = _utcnow
    _plans: dict[tuple[int, PlanSource], list[LibraryPlan]] = field(default_factory=dict)

    def put(self, plan: LibraryPlan) -> None:
        bucket = self._plans.setdefault((plan.library_id, plan.source), [])
        bucket.append(plan)
        if len(bucket) > self.keep:
            del bucket[: len(bucket) - self.keep]

    def latest(self, library_id: int, source: PlanSource) -> LibraryPlan | None:
        for plan in reversed(self._plans.get((library_id, source), [])):
            if not self._expired(plan):
                return plan
        return None

    def get(self, plan_id: str) -> LibraryPlan | None:
        """按标识查找; 不存在与已过期同样返回 None, 调用方不做区分."""
        for bucket in self._plans.values():
            for plan in bucket:
                if plan.plan_id == plan_id:
                    return None if self._expired(plan) else plan
        return None

    def drop_library(self, library_id: int) -> None:
        """库路径被修改或库被删除时丢弃该库清单."""
        for key in [key for key in self._plans if key[0] == library_id]:
            del self._plans[key]

    def _expired(self, plan: LibraryPlan) -> bool:
        return self.now() - plan.created_at > timedelta(seconds=self.ttl_seconds)


@dataclass
class _ScanState:
    entries: list[PlanEntry]
    dirs: dict[Path, DirCoverage]
    limit: int
    truncated: bool = False
    skipped_dirs: int = 0
    skipped_files: int = 0

    @property
    def full(self) -> bool:
        return len(self.entries) >= self.limit


@dataclass(frozen=True, slots=True)
class _DirResult:
    children: int
    disappears: bool


@in_thread
def scan_plan(
    scope_dir: Path,
    *,
    library_id: int,
    library_root: Path,
    recursive: bool,
    patterns: Sequence[str],
    scan: LibraryScan,
    source: PlanSource = PlanSource.RULES,
    limit: int = MAX_PLAN_ENTRIES,
) -> LibraryPlan:
    """遍历范围内的一层或整棵子树, 产出清单.

    - 黑名单与体积过小的文件是条目; 预告片与其余文件不是.
    - 磁盘上没有子项的目录作为「扫描时已空」的条目.
    - 回收站子树整棵不进入清单, 且算作不可删除的子项: 其父目录不会因此被预告清除.
    - 读不到的目录与 stat 失败的文件只计数, 其余子项继续.
    - 达到条目上限即停止遍历并标记已截断.
    """
    state = _ScanState(entries=[], dirs={}, limit=limit)
    _walk(scope_dir, state=state, scan=scan, recursive=recursive)
    plan = LibraryPlan(
        plan_id=new_plan_id(),
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
        skipped_dirs=state.skipped_dirs,
        skipped_files=state.skipped_files,
    )
    logger.info(
        "plan scanned",
        library_id=library_id,
        scope=str(scope_dir),
        entries=len(plan.entries),
        dirs=len(plan.dirs),
        truncated=plan.truncated,
        skipped_dirs=plan.skipped_dirs,
        skipped_files=plan.skipped_files,
    )
    return plan


def _walk(directory: Path, *, state: _ScanState, scan: LibraryScan, recursive: bool) -> _DirResult | None:
    """登记 ``directory`` 下的条目与覆盖信息; 读不到该目录时返回 None."""
    if state.truncated:
        return _DirResult(children=0, disappears=False)
    try:
        with os.scandir(directory) as scanned:
            children = list(scanned)
    except OSError as exc:
        state.skipped_dirs += 1
        logger.warning("plan scan directory unreadable", path=str(directory), error=str(exc))
        return None

    total = 0
    removed = 0
    for child in children:
        if state.truncated:
            break
        total += 1
        path = Path(child.path)
        if child.name == TRASH_DIRNAME:
            # 回收站: 不进清单, 也不计作会消失的子项.
            continue
        try:
            child_stat = child.stat(follow_symlinks=False)
        except OSError as exc:
            state.skipped_files += 1
            logger.warning("plan scan entry unreadable", path=str(path), error=str(exc))
            continue
        if stat.S_ISDIR(child_stat.st_mode) and not child.is_symlink():
            if _record_dir(path, state=state, scan=scan, recursive=recursive):
                removed += 1
            continue
        entry = _file_entry(path, scan=scan, child_stat=child_stat, is_symlink=child.is_symlink())
        if entry is not None and _record_entry(entry, state=state):
            removed += 1

    return _DirResult(children=total, disappears=total > 0 and removed == total)


def _record_dir(path: Path, *, state: _ScanState, scan: LibraryScan, recursive: bool) -> bool:
    """登记子目录; 返回该子目录会消失与否 (空目录作为条目, 也在此登记)."""
    if recursive:
        result = _walk(path, state=state, scan=scan, recursive=recursive)
        if result is None or state.truncated:
            return False
        if result.children == 0:
            entry = PlanEntry(path=path, kind=PlanEntryKind.DIR, reason=PlanReason.EMPTY_DIR)
            return _record_entry(entry, state=state)
        state.dirs[path] = DirCoverage(disk_children=result.children, will_be_empty=result.disappears)
        return result.disappears
    # 不递归时子目录不是处置对象: 计入子项数, 使父目录不会被预告清除.
    return False


def _file_entry(path: Path, *, scan: LibraryScan, child_stat: os.stat_result, is_symlink: bool) -> PlanEntry | None:
    kind = scan.unwanted_kind(path)
    if kind is None:
        return None
    return PlanEntry(
        path=path,
        kind=PlanEntryKind.SYMLINK if is_symlink else PlanEntryKind.FILE,
        reason=_REASONS[kind],
        size=child_stat.st_size,
        dev=child_stat.st_dev,
        ino=child_stat.st_ino,
        nlink=child_stat.st_nlink,
    )


def _record_entry(entry: PlanEntry, *, state: _ScanState) -> bool:
    if state.full:
        state.truncated = True
        return False
    state.entries.append(entry)
    return True
