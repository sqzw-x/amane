"""库内文件与目录的删除执行单元.

调用方给出目标与库根, 本模块只做磁盘操作: 不读配置, 不访问数据库,
不推导调用方未给出的路径 — 清单之外的路径一律拒绝.

边界判定使用字面路径 (``path_is_under``), 基准是调用方给出的库根, 不是当前库设置:
库内可能存在指向库外的符号链接, 解析真身会把库外内容纳入范围.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from ..utils.path import existing_disk_path, path_is_under
from ..utils.threads import in_thread
from .rules import TRASH_DIRNAME

logger = structlog.get_logger()

DeleteStatus = Literal["deleted", "changed", "failed"]

# Windows 上 junction 与挂载点卷带 reparse point 标记 (同卷时设备号不变), 其他平台的 stat 没有这两个字段.
_REPARSE_POINT_ATTRIBUTE: int = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


class _BoundaryRefusal(Exception):
    """目标跨越设备或 reparse point 边界, 或者设备无法判定."""


@dataclass(frozen=True)
class DeletedFile:
    """已从磁盘移除的普通文件. 释放空间按 ``(dev, ino)`` 归并后计算."""

    dev: int
    ino: int
    size: int
    nlink: int


@dataclass
class DeleteOutcome:
    """单个目标的删除结果. ``changed`` 表示磁盘上已经不存在."""

    status: DeleteStatus
    error: str | None = None
    files: list[DeletedFile] = field(default_factory=list)


@dataclass
class DeleteTally:
    """一批目标的结果计数.

    释放空间按 inode 归并: 只有当同一 inode 在执行集合内的名字数等于链接数时,
    删除才会真正释放数据 — 硬链接整理的源文件在库外, 不会被收进集合.
    """

    deleted: int = 0
    changed: int = 0
    failed: int = 0
    _groups: dict[tuple[int, int], list[DeletedFile]] = field(default_factory=dict, repr=False)

    def record(self, outcome: DeleteOutcome) -> None:
        match outcome.status:
            case "deleted":
                self.deleted += 1
            case "changed":
                self.changed += 1
            case "failed":
                self.failed += 1
        for item in outcome.files:
            self._groups.setdefault((item.dev, item.ino), []).append(item)

    @property
    def freed_bytes(self) -> int:
        """链接数不可用 (平台给 0) 时按 1 计: 宁可把可能未释放的数据算进来, 也不要恒为 0."""
        return sum(group[0].size for group in self._groups.values() if len(group) >= _links(group[0]))

    @property
    def hardlink_items(self) -> int:
        return sum(len(group) for group in self._groups.values() if len(group) != _links(group[0]))


@dataclass(frozen=True)
class PruneResult:
    removed: int = 0
    failed: int = 0


@in_thread
def delete_target(path: Path, *, library_root: Path) -> DeleteOutcome:
    """删除一个目标 (文件、符号链接或目录).

    库根外的一切一律拒绝, 没有例外; 库根自身与 ``.amane_trash`` 自身同样拒绝.
    """
    disk_path = existing_disk_path(path, follow_symlinks=False)
    if disk_path is None:
        return DeleteOutcome(status="changed")

    refusal = _refuse_reason(disk_path, library_root=library_root)
    if refusal is not None:
        logger.warning("delete refused", path=str(disk_path), reason=refusal)
        return DeleteOutcome(status="failed", error=refusal)

    files: list[DeletedFile] = []
    try:
        # 先判符号链接: 断链链接正是要删除的对象, 而 is_dir 会跟随目标.
        if disk_path.is_symlink():
            disk_path.unlink()
        elif disk_path.is_dir():
            _remove_tree(disk_path, files)
        else:
            _remove_file(disk_path, files)
    except (OSError, _BoundaryRefusal) as exc:
        logger.warning("delete failed", path=str(disk_path), error=str(exc))
        return DeleteOutcome(status="failed", error=str(exc), files=files)
    return DeleteOutcome(status="deleted", files=files)


def _links(item: DeletedFile) -> int:
    return max(item.nlink, 1)


def ancestor_dirs(path: Path, *, library_root: Path) -> list[Path]:
    """``path`` 到库根之间的祖先目录, 由深到浅.

    不含库根与 ``.amane_trash``; 传入路径不在库内时返回空列表 —
    剪枝只在库内进行, 库外目录不在清单的授权范围内.
    """
    if not path_is_under(path, library_root):
        return []
    trash_root = library_root / TRASH_DIRNAME
    out: list[Path] = []
    for parent in Path(path).parents:
        if not path_is_under(parent, library_root):
            break
        if same_path(parent, library_root) or same_path(parent, trash_root):
            break
        out.append(parent)
    return out


@in_thread
def prune_empty_dirs(directories: Iterable[Path], *, library_root: Path) -> PruneResult:
    """自底向上删除已经空的目录, 只尝试传入的目录, 不向库根扩展.

    只有 ``ENOTEMPTY`` 视为「非空」并跳过: 依赖目录删除对非空目录报错来保证确实为空,
    权限、忙、只读等其余错误记入 ``failed``, 不当作「非空」静默停下.
    """
    removed = 0
    failed = 0
    trash_root = library_root / TRASH_DIRNAME
    candidates = sorted({Path(directory) for directory in directories}, key=_depth, reverse=True)
    for directory in candidates:
        if same_path(directory, library_root) or same_path(directory, trash_root):
            continue
        if not path_is_under(directory, library_root):
            continue
        try:
            directory.rmdir()
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                continue
            failed += 1
            logger.warning("prune failed", path=str(directory), error=str(exc))
            continue
        removed += 1
    return PruneResult(removed=removed, failed=failed)


def _depth(path: Path) -> int:
    return len(path.parts)


def _remove_file(path: Path, files: list[DeletedFile]) -> None:
    st = path.lstat()
    path.unlink()
    files.append(DeletedFile(dev=st.st_dev, ino=st.st_ino, size=st.st_size, nlink=st.st_nlink))


def _remove_tree(target: Path, files: list[DeletedFile]) -> None:
    """删除目录内容后删除目录本身; 不跟随符号链接, 不跨越挂载点边界."""
    target_stat = target.lstat()
    parent_stat = target.parent.lstat()
    if _crosses_boundary(target_stat, dev=parent_stat.st_dev):
        raise _BoundaryRefusal(f"target is a mount point: {target}")
    _remove_contents(target, dev=target_stat.st_dev, files=files)
    target.rmdir()


def _remove_contents(directory: Path, *, dev: int, files: list[DeletedFile]) -> None:
    with os.scandir(directory) as entries:
        for entry in entries:
            path = Path(entry.path)
            # 真正的文件查询: 目录枚举顺带给出的统计在 Windows 上不含设备 / 文件编号 / 链接数.
            entry_stat = path.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                path.unlink()
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if entry.name == TRASH_DIRNAME:
                    raise _BoundaryRefusal(f"refuse to delete trash subtree: {path}")
                if _crosses_boundary(entry_stat, dev=dev):
                    raise _BoundaryRefusal(f"mount boundary crossed: {path}")
                _remove_contents(path, dev=dev, files=files)
                path.rmdir()
                continue
            path.unlink()
            files.append(
                DeletedFile(
                    dev=entry_stat.st_dev,
                    ino=entry_stat.st_ino,
                    size=entry_stat.st_size,
                    nlink=entry_stat.st_nlink,
                )
            )


def _is_reparse_point(st: os.stat_result) -> bool:
    attributes: int = getattr(st, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _crosses_boundary(st: os.stat_result, *, dev: int) -> bool:
    """子项是否跨越设备或 reparse point 边界. 挂载盘断连时 lstat 先抛错, 同样记为失败."""
    return st.st_dev != dev or _is_reparse_point(st)


def _refuse_reason(target: Path, *, library_root: Path) -> str | None:
    if same_path(target, library_root):
        return f"refuse to delete library root: {target}"
    if same_path(target, library_root / TRASH_DIRNAME):
        return f"refuse to delete trash root: {target}"
    if path_is_under(target, library_root):
        return None
    return f"outside library root: {target}"


def same_path(left: Path, right: Path) -> bool:
    """字面路径是否相等. ``path_is_under`` 已比较 NFC、大小写与归一, 双向包含即相等."""
    return path_is_under(left, right) and path_is_under(right, left)
