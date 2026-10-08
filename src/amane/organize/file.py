from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from ..enums import MoveMode
from ..utils.path import existing_disk_path
from ..utils.threads import in_thread

logger = structlog.get_logger()


class PlaceOutcome(StrEnum):
    """PLACED 已落盘或已就位; CONFLICT 目标被占用, 未处理; FAILED 执行失败."""

    PLACED = "placed"
    CONFLICT = "conflict"
    FAILED = "failed"


@dataclass
class OrganizeResult:
    outcome: PlaceOutcome
    dest: Path | None = None
    """PLACED 是落盘路径; CONFLICT 是被占用的目标路径; FAILED 时可能是已落盘路径 (链接失败仍回写)."""
    error: str | None = None


@in_thread
def target_occupied(source: Path, dest: Path) -> bool:
    """`dest` 上已有文件且与 `source` 不是同一个文件.

    同一个文件含硬链与符号链接解析: 源已在落点上时不构成冲突.
    无法比较 (stat 失败等) 时按占用处理, 不动磁盘上已有的东西.
    """
    dest_on_disk = existing_disk_path(dest)
    if dest_on_disk is None:
        return False
    try:
        return not source.samefile(dest_on_disk)
    except OSError:
        return True


@in_thread
def execute_organize(
    source: Path,
    target_dir: Path,
    target_stem: str,
    mode: MoveMode = MoveMode.MOVE,
    *,
    suffix: str | None = None,
) -> OrganizeResult:
    """落盘单元, 一次一个文件.

    目标被占用时不改名、不动磁盘, 返回 CONFLICT 交给调用方记账; 冲突不自动解决.
    """
    disk_source = existing_disk_path(source)
    if disk_source is None:
        logger.warning("organize source not found", source=str(source))
        return OrganizeResult(outcome=PlaceOutcome.FAILED, error=f"源文件不存在: {source}")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_suffix = disk_source.suffix if suffix is None else suffix
        dest = target_dir / f"{target_stem}{dest_suffix}"
        if existing_disk_path(dest) is not None:
            # 已就位则无需动作.
            if not target_occupied.sync(disk_source, dest):
                return OrganizeResult(outcome=PlaceOutcome.PLACED, dest=dest)
            logger.warning("organize target occupied", source=str(disk_source), dest=str(dest))
            return OrganizeResult(outcome=PlaceOutcome.CONFLICT, dest=dest)

        match mode:
            case MoveMode.MOVE:
                disk_source.move(dest)
            case MoveMode.COPY:
                disk_source.copy(dest)
            case MoveMode.HARDLINK:
                dest.hardlink_to(disk_source)
            case MoveMode.SYMLINK:
                dest.symlink_to(disk_source)

        logger.debug("file organized", source=source.name, dest=str(dest), mode=mode)
        return OrganizeResult(outcome=PlaceOutcome.PLACED, dest=dest)

    except Exception as e:
        logger.error("organize failed", source=str(source), target_dir=str(target_dir), error=str(e))
        return OrganizeResult(outcome=PlaceOutcome.FAILED, error=str(e))
