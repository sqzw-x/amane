from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from ..enums import MoveMode
from ..library.rules import POINTER_EXTENSIONS
from ..utils.path import existing_disk_path
from ..utils.threads import in_thread

logger = structlog.get_logger()


class PlaceOutcome(StrEnum):
    """PLACED 含「源已在目标路径上」的就位情形; CONFLICT 是目标被别的文件占用而未处理."""

    PLACED = "placed"
    CONFLICT = "conflict"
    FAILED = "failed"


class TargetState(StrEnum):
    """目标路径的占用判定结果."""

    FREE = "free"
    """目标位置上没有条目, 可以直接落盘."""
    SAME = "same"
    """目标位置上就是源本身: 同一文件, 含硬链与符号链接解析."""
    OCCUPIED = "occupied"
    """目标位置上另有条目."""
    UNKNOWN = "unknown"
    """读不出状态: 比较源与目标时 stat 失败, 或两者在两次读取之间被替换."""


@dataclass(frozen=True, slots=True)
class TargetCheck:
    state: TargetState
    error: str | None = None
    """UNKNOWN 时的错误说明, 含失败路径与 errno; 展示用, 不解析."""


@dataclass
class OrganizeResult:
    outcome: PlaceOutcome
    dest: Path | None = None
    """PLACED 是落盘路径; CONFLICT 是被占用的目标路径; FAILED 时可能是已落盘路径 (链接失败仍回写)."""
    error: str | None = None


@in_thread
def target_state(source: Path, dest: Path) -> TargetCheck:
    """判定 `dest` 位置能否落盘, 只读磁盘.

    同一个文件含硬链与符号链接解析: 源已在目标路径上时记为 SAME, 不构成冲突.
    读不出状态时记为 UNKNOWN 并带上错误说明, 不得当作被占用: 那会让用户去排查一个与本次无关的文件.
    """
    try:
        dest_on_disk = existing_disk_path(dest)
        if dest_on_disk is None:
            return TargetCheck(TargetState.FREE)
        if source.samefile(dest_on_disk):
            return TargetCheck(TargetState.SAME)
        return TargetCheck(TargetState.OCCUPIED)
    except OSError as e:
        return TargetCheck(TargetState.UNKNOWN, error=f"无法判定目标路径是否被占用: {e}")


def video_dest(target_dir: Path, target_stem: str, source: Path, suffix: str | None = None) -> Path:
    """目标路径的唯一算法: 后缀默认取源文件, 而不是模板里写死的那个.

    占用判定与真正的落盘都走这里, 否则模板写死扩展名时两处会算出不同路径.
    """
    return target_dir / f"{target_stem}{source.suffix if suffix is None else suffix}"


@in_thread
def is_rebuildable_pointer(source: Path, dest: Path) -> bool:
    """目标是一份可由源重建的指针: 两侧都是 `.strm`, 且目标是普通文件.

    `.strm` 只写一行播放地址, 内容由源决定, 不承载用户数据; 上游每天刷新源时,
    目标上的旧指针必须跟着换, 否则媒体服务器一直读到旧地址.
    符号链接与目录一律不算: 它们不是上一轮整理写出的指针产物.
    """
    return (
        source.suffix.lower() in POINTER_EXTENSIONS
        and dest.suffix.lower() in POINTER_EXTENSIONS
        and not dest.is_symlink()
        and dest.is_file()
    )


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
    例外是 `.strm` 指针: 目标由源重建, 内容不同即换掉.
    无法判定时返回 FAILED 并带上错误说明, 不改动磁盘.
    """
    disk_source = existing_disk_path(source)
    if disk_source is None:
        logger.warning("organize source not found", source=str(source))
        return OrganizeResult(outcome=PlaceOutcome.FAILED, error=f"源文件不存在: {source}")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = video_dest(target_dir, target_stem, disk_source, suffix)
        check = target_state.sync(disk_source, dest)
        match check.state:
            case TargetState.SAME:
                # 已就位则无需动作.
                return OrganizeResult(outcome=PlaceOutcome.PLACED, dest=dest)
            case TargetState.OCCUPIED if is_rebuildable_pointer.sync(disk_source, dest):
                # 旧指针是上一轮整理的产物: 内容一致即已就位, 不一致则移除, 由本轮重新落盘.
                if disk_source.read_bytes() == dest.read_bytes():
                    if mode is MoveMode.MOVE:
                        disk_source.unlink()
                    return OrganizeResult(outcome=PlaceOutcome.PLACED, dest=dest)
                logger.info("organize replacing stale pointer", source=str(disk_source), dest=str(dest))
                dest.unlink()
            case TargetState.OCCUPIED:
                logger.warning("organize target occupied", source=str(disk_source), dest=str(dest))
                return OrganizeResult(outcome=PlaceOutcome.CONFLICT, dest=dest)
            case TargetState.UNKNOWN:
                logger.warning(
                    "organize target state unknown", source=str(disk_source), dest=str(dest), error=check.error
                )
                return OrganizeResult(outcome=PlaceOutcome.FAILED, error=check.error)
            case TargetState.FREE:
                pass

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
