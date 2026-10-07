"""残留目录的判定: 视频被搬走后只留下附属文件的目录.

判定是三个条件的合取, 全部由 ``src/amane/library/cleanup/inventory.py::_walk`` 在同一次递归里算出, 不额外遍历磁盘:

- 每一级祖先目录 (含库根) 的**直接子项**里没有媒体;
- 目录的整棵子树里没有媒体;
- 子树里每个文件要么是附属文件, 要么是可随目录删除的垃圾项, 且没有不可删除的子项.

库根与扫描范围目录不判定 (一条目录条目就能带走整库内容), 它们的附属文件逐条登记, 条件只取
本层: 库内别处有正片与这一层的文件是不是残留无关.

三个条件都只描述磁盘现状, 索引不参与. 残留条目在执行前按同一套条件重算
(``src/amane/handlers/delete.py::_reverify``), 拦住扫描之后才落进正片的目录.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ..rules import DEFAULT_SUBTITLE_EXTENSIONS, MEDIA_EXTENSIONS, compile_skip_patterns, is_in_trash

if TYPE_CHECKING:
    from re import Pattern

# 子树内最新 mtime 距今未超过该值时不判定, 覆盖「附属文件先落盘, 视频尚未到达」的窗口.
# 不设配置项: 扫描由用户触发, 阈值只影响「刚变动的目录晚一小时才出现在清单里」.
ORPHAN_COOLDOWN_SECONDS = 3600

# 附属文件: 视频旁边由 Amane 产出、或供媒体服务器读取的文件.
COMPANION_EXTENSIONS = frozenset(
    {
        ".nfo",
        # VobSub 的字幕索引与 .sub 成对; 不在 DEFAULT_SUBTITLE_EXTENSIONS 里, 缺了它这类残留无法清除.
        ".idx",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".avif",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".jfif",
        # Kodi 的缩略图.
        ".tbn",
    }
)

# 可随目录删除的垃圾项: 不否决判定, 也不在遍历里单独登记 — 残留目录命中时随内容登记为
# `noise` 条目, 与父目录一起删除. 库根与扫描范围目录只登记附属文件, 这两层的垃圾项不登记.
# 按整个文件名比较, 不按扩展名 — `.DS_Store` 没有扩展名, 而按扩展名匹配会把 library.db 当垃圾.
_JUNK_FILENAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
# macOS 在非原生文件系统上的伴生文件 (._.DS_Store、._README 都没有可用的扩展名).
_JUNK_PREFIXES = ("._",)
_JUNK_EXTENSIONS = frozenset({".tmp"})

# 不可删除的子项: 命中即否决该目录. 目录名与文件扩展名分开, 效力与 kind 无关.
_VETO_DIRNAMES = frozenset({".amane_trash", "#recycle", ".stversions", ".stfolder", "@eadir", ".appledouble"})
# 暂停或排队中的下载进度: 冷却期只保护正在写入的那一个, 删掉不可恢复.
_VETO_EXTENSIONS = frozenset({".part", ".crdownload"})


class JunkKind(StrEnum):
    """垃圾项的两个类别. 效力与它无关, 只决定匹配方式与是否否决."""

    DELETABLE = "deletable"
    """可随目录删除: 不否决判定; 残留目录命中时随内容登记为 `noise` 条目, 一并删除."""
    VETO = "veto"
    """不可删除的子项: 否决该目录."""


class BlockedReason(StrEnum):
    """目录被判成候选但没有登记的原因.

    只暴露用户能据此行动的那一种: 子树里有无法识别的文件. 其余原因 (目录里有媒体、
    有不可删除的子项、仍在冷却期) 都是内部保护规则, 对应的目录与正常媒体目录同样不处理,
    不需要向用户解释.
    """

    UNEXPLAINED = "unexplained"
    """子树里有白名单外的文件: 用户要么清掉它, 要么它就是不该被清的内容."""


@dataclass(frozen=True, slots=True)
class BlockedDirs:
    """未登记的候选目录数."""

    unexplained: int = 0

    @property
    def total(self) -> int:
        return self.unexplained

    def plus(self, reason: BlockedReason) -> BlockedDirs:
        match reason:
            case BlockedReason.UNEXPLAINED:
                return BlockedDirs(self.unexplained + 1)


@dataclass(frozen=True, slots=True)
class OrphanVerdict:
    """一个目录的判定结果: 可登记, 或者是未登记的原因 (不是候选时为 None)."""

    blocked: BlockedReason | None = None

    @property
    def registrable(self) -> bool:
        return self.blocked is None


@dataclass(frozen=True, slots=True)
class OrphanScan:
    """残留判定的库设置. 只读, 由调用方按当前库构造; 不传则不判定 (不涉及残留的调用方无需改动)."""

    library_root: Path
    scope_dir: Path
    subtitle_extensions: tuple[str, ...] = DEFAULT_SUBTITLE_EXTENSIONS
    trailer_pattern: str | None = None
    patterns: Sequence[str] = ()
    media_extensions: frozenset[str] = MEDIA_EXTENSIONS

    @classmethod
    def from_library(
        cls,
        *,
        library_root: Path,
        scope_dir: Path,
        subtitle_extensions: Sequence[str],
        trailer_pattern: str | None,
        patterns: Sequence[str],
        media_extensions: frozenset[str] | None,
    ) -> OrphanScan:
        """字幕取库设置与默认值的并集: 清空它表示关闭字幕发现, 不代表 `.srt` 不再是附属文件."""
        merged = tuple(dict.fromkeys([*subtitle_extensions, *DEFAULT_SUBTITLE_EXTENSIONS]))
        return cls(
            library_root=library_root,
            scope_dir=scope_dir,
            subtitle_extensions=merged,
            trailer_pattern=trailer_pattern,
            patterns=tuple(patterns),
            media_extensions=media_extensions or MEDIA_EXTENSIONS,
        )

    @property
    def companion_extensions(self) -> frozenset[str]:
        return COMPANION_EXTENSIONS | frozenset(ext.lower() for ext in self.subtitle_extensions)

    def trailer_matcher(self) -> Pattern[str] | None:
        """预告片正则只编译一次: 判定在万级子项上反复调用."""
        compiled = compile_skip_patterns([self.trailer_pattern])
        return compiled[0] if compiled else None

    def is_media(self, path: Path, *, trailer: Pattern[str] | None = None) -> bool:
        """库会当作影片接收的路径.

        不套用黑名单与体积: 命中黑名单的视频同样是库内的一份内容, 排除它会让只含
        `sample.mp4` 的目录被整目录删除. 扩展名与预告片先于 `patterns`: 用户可以把 `.mp4`
        写进 `subtitle_extensions`, 而 `patterns` 非空的库里不匹配 `patterns` 的 `.mp4` 不算媒体,
        于是白名单成员会被当成残留.
        """
        if is_in_trash(path):
            return False
        if path.suffix.lower() in self.media_extensions:
            return True
        if trailer is not None and trailer.search(path.name):
            return True
        return any(path.match(pattern) for pattern in self.patterns)

    def is_companion(self, path: Path) -> bool:
        return path.suffix.lower() in self.companion_extensions

    def verdict(
        self,
        directory: Path,
        *,
        ancestor_has_media: bool,
        subtree_has_media: bool,
        subtree_explainable: bool,
        subtree_undeletable: bool,
        subtree_has_content: bool,
        newest_mtime: float,
        directory_mtime: float,
        now: float,
    ) -> OrphanVerdict | None:
        """目录能否登记为残留条目; 不是候选 (库根与扫描范围目录) 时返回 None.

        库根与扫描范围目录永不登记: 它们是删除的保留目标, 一条条目就能带走整库内容.
        调用方对这两层退化为逐文件登记, 条件按本层算.

        三个条件里的媒体判据都在子树口径上: 目录自己那层的直接子项包含在子树里, 因此不需要
        单独的参数.
        """
        if directory == self.library_root or directory == self.scope_dir:
            return None
        if ancestor_has_media or subtree_has_media:
            return None
        if subtree_undeletable:
            # 有不可删除的子项 (回收站、版本库、下载进度): 整个目录不碰.
            return None
        if not subtree_has_content:
            # 子树里只有空目录: 没有内容可清, 交给空目录条目.
            return None
        # 白名单外的文件先于冷却期: 未登记的原因只按用户能据此行动的那一种算, 冷却期中的目录
        # 同样计入 `BlockedDirs.unexplained`.
        if not subtree_explainable:
            return OrphanVerdict(BlockedReason.UNEXPLAINED)
        # 阈值处算「仍在冷却期」; 未来时间戳 (时钟偏移) 同样按未超阈值处理.
        if now - max(newest_mtime, directory_mtime) <= ORPHAN_COOLDOWN_SECONDS:
            return None
        return OrphanVerdict()


def classify_junk(path: Path, *, is_dir: bool) -> JunkKind | None:
    """垃圾项分类; 不是垃圾项返回 None.

    精确名字一律按整个文件名比较: `.DS_Store` 没有扩展名 (``suffix == ""``), 按扩展名永远
    匹配不上, 而按扩展名匹配会把任意 `.db` / `.ini` 当垃圾随目录一起删掉.
    """
    name = path.name.casefold()
    if is_dir:
        return JunkKind.VETO if name in _VETO_DIRNAMES else None
    if name in _JUNK_FILENAMES or path.suffix.lower() in _JUNK_EXTENSIONS:
        return JunkKind.DELETABLE
    if path.suffix.lower() in _VETO_EXTENSIONS:
        return JunkKind.VETO
    if name.startswith(_JUNK_PREFIXES):
        return JunkKind.DELETABLE
    return None
