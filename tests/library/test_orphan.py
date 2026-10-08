"""残留目录判定: 子树只含附属文件与可随目录删除的垃圾项的目录进清理清单, 其余一律不判定.

判定同时覆盖三个方向 (祖先的直接子项、自身、子树), 因此用例按方向分组, 并单独覆盖
登记形态 (嵌套候选、内部条目保留、库根与扫描范围目录的退化) 与冷却期边界.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from amane.library import (
    ORPHAN_COOLDOWN_SECONDS,
    TRASH_DIRNAME,
    CleanupInventory,
    InventoryEntryKind,
    InventoryReason,
    LibraryScan,
    OrphanScan,
    build_inventory_tree,
    find_inventory_node,
    scan_inventory,
)
from amane.library.cleanup import inventory as inventory_module
from tests.helpers import make_junction

# 判定用的固定时刻; 夹具文件的 mtime 统一设成它, 冷却期因此不影响默认用例.
_NOW = 1_800_000_000.0


def _orphan_scan(
    lib: Path,
    *,
    scope: Path | None = None,
    subtitles: list[str] | None = None,
    patterns: list[str] | None = None,
) -> OrphanScan:
    return OrphanScan.from_library(
        library_root=lib,
        scope_dir=scope or lib,
        subtitle_extensions=subtitles if subtitles is not None else [".srt", ".ass", ".ssa", ".vtt", ".sub"],
        trailer_pattern="(?i)trailer",
        patterns=patterns or [],
        media_extensions=None,
    )


def _touch(path: Path, *, mtime: float = _NOW - 7200, size: int = 4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _scan(
    lib: Path,
    *,
    scope: Path | None = None,
    subtitles: list[str] | None = None,
    patterns: list[str] | None = None,
    recursive: bool = True,
    limit: int = 20000,
    blacklist: list[str] | None = None,
    orphan: bool = True,
    now: float = _NOW,
) -> CleanupInventory:
    scope_dir = scope or lib
    return scan_inventory.sync(
        scope_dir,
        library_id=1,
        library_root=lib,
        recursive=recursive,
        patterns=patterns or [],
        scan=LibraryScan(blacklist_patterns=blacklist, trailer_pattern="(?i)trailer", patterns=patterns),
        limit=limit,
        orphan_scan=_orphan_scan(lib, scope=scope, subtitles=subtitles, patterns=patterns) if orphan else None,
        now=now,
    )


def _touch_at(path: Path, *, mtime: float) -> Path:
    """写入并把文件与其父目录的 mtime 定到给定时刻.

    目录自身的 mtime 也必须定: 否则夹具目录带着真实时间, 冷却期用例无从构造边界.
    """
    _touch(path, mtime=mtime)
    os.utime(path.parent, (mtime, mtime))
    return path


def _reasons(inventory: CleanupInventory, lib: Path) -> dict[str, InventoryReason]:
    """执行集合里的条目 (容器条目也在内, 它承载徽章与整目录汇总)."""
    return {entry.path.relative_to(lib).as_posix(): entry.reason for entry in inventory.entries}


def _sizes(inventory: CleanupInventory, lib: Path) -> dict[str, int | None]:
    return {entry.path.relative_to(lib).as_posix(): entry.size for entry in inventory.entries}


class TestOrphanVerdict:
    def test_companion_only_dir_is_registered(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.nfo")
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "NSFS-039.zh.srt")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {
            "old": InventoryReason.ORPHAN,
            "old/NSFS-039.nfo": InventoryReason.ORPHAN,
            "old/NSFS-039.zh.srt": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
        }
        assert _sizes(inventory, lib)["old"] == 12

    def test_companion_files_under_subdir(self, tmp_path: Path) -> None:
        """容器是最外层的候选, 子目录里的附属文件上浮成它的条目."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "images" / "poster.jpg")
        _touch(lib / "old" / "NSFS-039.nfo")

        assert _reasons(_scan(lib), lib) == {
            "old": InventoryReason.ORPHAN,
            "old/NSFS-039.nfo": InventoryReason.ORPHAN,
            "old/images/poster.jpg": InventoryReason.ORPHAN,
        }

    def test_media_in_subdir_blocks_parent(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.nfo")
        _touch(lib / "old" / "CD2" / "NSFS-039-CD2.mp4")

        assert _reasons(_scan(lib), lib) == {}

    def test_media_in_same_dir_blocks(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.nfo")
        _touch(lib / "old" / "NSFS-039.mp4")

        assert _reasons(_scan(lib), lib) == {}

    def test_sibling_media_does_not_block_by_enumeration_order(self, tmp_path: Path) -> None:
        """兄弟目录之间不是祖先关系.

        「祖先直接子项含媒体」必须在同一层里一次算完: 边下钻边更新会让排在媒体之后的兄弟
        被当成「祖先含媒体」, 判定结果因此取决于目录枚举顺序.
        """
        lib = tmp_path / "lib"
        _touch(lib / "a_media" / "NSFS-001.mp4")
        _touch(lib / "z_orphan" / "NSFS-039.nfo")

        assert _reasons(_scan(lib), lib) == {
            "z_orphan": InventoryReason.ORPHAN,
            "z_orphan/NSFS-039.nfo": InventoryReason.ORPHAN,
        }

    def test_media_in_ancestor_blocks_subdir(self, tmp_path: Path) -> None:
        """extrafanart 这类附属子目录由直接父目录里的正片保护."""
        lib = tmp_path / "lib"
        _touch(lib / "work" / "NSFS-039.mp4")
        _touch(lib / "work" / "extrafanart" / "1.jpg")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.total == 0

    def test_sibling_dir_still_registered_when_ancestor_has_media(self, tmp_path: Path) -> None:
        """祖先有媒体只否决它的后代, 不否决祖先的兄弟."""
        lib = tmp_path / "lib"
        _touch(lib / "work" / "NSFS-039.mp4")
        _touch(lib / "old" / "NSFS-039.nfo")

        assert _reasons(_scan(lib), lib) == {
            "old": InventoryReason.ORPHAN,
            "old/NSFS-039.nfo": InventoryReason.ORPHAN,
        }

    def test_root_companion_of_live_media_is_not_registered(self, tmp_path: Path) -> None:
        """库根的直接子项里有正片时, 根层的 NFO 是它的附属文件."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-001.mp4")
        _touch(lib / "NSFS-001.nfo")

        assert _reasons(_scan(lib), lib) == {}

    def test_root_media_blocks_subdirs(self, tmp_path: Path) -> None:
        """库根直接子项里有媒体时, 子目录一律不判定 (平铺库不会误删)."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-001.mp4")
        _touch(lib / "old" / "NSFS-039.nfo")

        assert _reasons(_scan(lib), lib) == {}

    def test_trailer_counts_as_media(self, tmp_path: Path) -> None:
        """预告片在 classify 里是 SKIP; 判定不能因此把只含它的目录当成残留."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "trailer.mp4")

        assert _reasons(_scan(lib), lib) == {}

    def test_blacklisted_video_blocks_orphan_and_stays_an_entry(self, tmp_path: Path) -> None:
        """命中黑名单的视频同样是库内的一份内容: 目录不成残留, 它自己仍是黑名单条目."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "sample.mp4")
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib, blacklist=["sample"])

        assert _reasons(inventory, lib) == {"old/sample.mp4": InventoryReason.BLACKLIST}

    def test_video_extension_in_subtitle_whitelist_still_media(self, tmp_path: Path) -> None:
        """用户可以把 .mp4 写进 subtitle_extensions; 扩展名检查必须先于白名单."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.mp4")

        inventory = _scan(lib, subtitles=[".mp4"])

        assert _reasons(inventory, lib) == {}

    def test_patterns_library_media_via_pattern(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.weird")
        _touch(lib / "old" / "poster.jpg")

        assert _reasons(_scan(lib, patterns=["**/*.weird"]), lib) == {}

    def test_recursive_false_registers_nothing(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "sub" / "NSFS-039.mp4")

        assert _reasons(_scan(lib, recursive=False), lib) == {}

    def test_orphan_scan_absent_keeps_old_behaviour(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")

        assert _reasons(_scan(lib, orphan=False), lib) == {}


class TestCompanionWhitelist:
    @pytest.mark.parametrize(
        "name",
        ["NSFS-039.nfo", "POSTER.JPG", "cover.avif", "cover.HEIC", "NSFS-039.idx", "NSFS-039.ass", "thumb.tbn"],
    )
    def test_whitelisted_names(self, tmp_path: Path, name: str) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / name)

        assert _reasons(_scan(lib), lib) == {
            "old": InventoryReason.ORPHAN,
            f"old/{name}": InventoryReason.ORPHAN,
        }

    def test_empty_subtitle_extensions_falls_back_to_defaults(self, tmp_path: Path) -> None:
        """清空字幕扩展名表示关闭字幕发现, 不代表 .srt 不再是附属文件."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "NSFS-039.srt")

        assert _reasons(_scan(lib, subtitles=[]), lib) == {
            "old": InventoryReason.ORPHAN,
            "old/NSFS-039.srt": InventoryReason.ORPHAN,
        }

    def test_extensionless_file_blocks(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "README")

        assert _reasons(_scan(lib), lib) == {}


class TestJunk:
    @pytest.mark.parametrize("name", [".DS_Store", "Thumbs.db", "desktop.ini", "notes.tmp", "._README", "._.DS_Store"])
    def test_deletable_junk_is_listed_as_noise(self, tmp_path: Path, name: str) -> None:
        """垃圾文件同样会被删除, 因此也登记为条目; 标记为 noise 供面板默认折叠."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / name)
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib)
        node = find_inventory_node(build_inventory_tree(inventory), lib / "old")

        assert _sizes(inventory, lib)["old"] == 4
        assert node is not None
        assert [(child.name, child.noise) for child in node.children] == [(name, True), ("poster.jpg", False)]

    def test_apple_double_video_sidecar_is_junk_not_media(self, tmp_path: Path) -> None:
        """._x.mp4 是 macOS 伴生文件; 垃圾项判定必须先于媒体判据."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "._NSFS-039.mp4")
        _touch(lib / "old" / "poster.jpg")

        assert _reasons(_scan(lib), lib) == {
            "old": InventoryReason.ORPHAN,
            "old/._NSFS-039.mp4": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
        }

    @pytest.mark.parametrize("name", ["library.db", "settings.ini", "notes.tmp.bak"])
    def test_lookalike_names_are_not_junk(self, tmp_path: Path, name: str) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / name)

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.unexplained == 1

    @pytest.mark.parametrize(
        "name", ["#recycle", ".stversions", ".stfolder", "@eaDir", ".AppleDouble", "#Recycle", "@EADIR"]
    )
    def test_veto_dirs_are_left_alone(self, tmp_path: Path, name: str) -> None:
        """有不可删除子项的目录与正常媒体目录同样不处理: 不登记, 也不计数; 匹配大小写不敏感."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / name / "inner.bin")
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.total == 0

    @pytest.mark.parametrize("name", ["NSFS-039.part", "x.crdownload"])
    def test_veto_files_are_left_alone(self, tmp_path: Path, name: str) -> None:
        """暂停中的下载进度删掉不可恢复: 该目录整个不碰."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / name)
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.total == 0

    def test_junk_only_dir_is_not_registered_as_empty(self, tmp_path: Path) -> None:
        """只含垃圾项的目录不是空的: 登记成空目录会让删除动作带走整棵子树."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / ".DS_Store")

        inventory = _scan(lib)

        # 它不作为空目录登记: 空目录条目会连带整棵子树被删, 而这里只该删垃圾文件与目录本身.
        assert InventoryReason.EMPTY_DIR not in _reasons(inventory, lib).values()
        # 垃圾文件不会被删 (它随目录一起删), 因此这条条目执行完目录并不空.
        assert inventory.dirs[lib / "old"].will_be_empty is False


class TestCooldown:
    def test_recent_subtree_is_not_registered(self, tmp_path: Path) -> None:
        """冷却期内的目录按「刚变动过」处理, 不登记也不计数."""
        lib = tmp_path / "lib"
        _touch_at(lib / "old" / "poster.jpg", mtime=_NOW - 60)

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.total == 0

    def test_exactly_at_threshold_is_not_registered(self, tmp_path: Path) -> None:
        """阈值处算「仍在冷却期」; 目录自身的 mtime 单独设旧, 让边界只由文件决定."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg", mtime=_NOW - ORPHAN_COOLDOWN_SECONDS)
        os.utime(lib / "old", (_NOW - 7200, _NOW - 7200))

        assert _reasons(_scan(lib), lib) == {}

    def test_just_past_threshold_is_registered(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch_at(lib / "old" / "poster.jpg", mtime=_NOW - ORPHAN_COOLDOWN_SECONDS - 1)

        assert _reasons(_scan(lib), lib) == {
            "old": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
        }

    def test_directory_mtime_counts(self, tmp_path: Path) -> None:
        """目录自身的 mtime 来自父目录那次 lstat, 也必须参与冷却期."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg", mtime=_NOW - 7200)
        os.utime(lib / "old", (_NOW - 60, _NOW - 60))

        assert _reasons(_scan(lib), lib) == {}

    def test_future_mtime_is_treated_as_in_cooldown(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch_at(lib / "old" / "poster.jpg", mtime=_NOW + 3600)

        assert _reasons(_scan(lib), lib) == {}

    def test_cooldown_does_not_affect_blacklist_entries(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "ad-1.mkv", mtime=_NOW - 60)
        os.utime(lib / "old", (_NOW - 60, _NOW - 60))

        inventory = _scan(lib, blacklist=["ad-"])

        assert _reasons(inventory, lib) == {"old/ad-1.mkv": InventoryReason.BLACKLIST}


class TestRegistration:
    def test_nested_candidates_register_outermost_only(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg", size=10)
        _touch(lib / "old" / "extrafanart" / "1.jpg", size=20)

        inventory = _scan(lib)

        assert _sizes(inventory, lib)["old"] == 30
        assert _reasons(inventory, lib) == {
            "old": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
            "old/extrafanart/1.jpg": InventoryReason.ORPHAN,
        }

    def test_inner_entries_are_kept(self, tmp_path: Path) -> None:
        """候选内部的空目录与黑名单文件保留为条目: 保留条目才能使面板列出并删除这些内容."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg", size=10)
        _touch(lib / "old" / "sample.jpg", size=20)
        (lib / "old" / "emptysub").mkdir(parents=True)

        inventory = _scan(lib, blacklist=["sample"])

        assert _sizes(inventory, lib)["old"] == 30
        assert _reasons(inventory, lib) == {
            "old": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
            "old/sample.jpg": InventoryReason.BLACKLIST,
            "old/emptysub": InventoryReason.EMPTY_DIR,
        }
        assert inventory.truncated is False
        assert inventory.dropped == 0
        # 容器条目排在它保留的内容之后: 触顶丢弃的是内容条目, 容器占住的位置保留.
        assert inventory.entries[-1].path == lib / "old"
        node = find_inventory_node(build_inventory_tree(inventory), lib / "old")
        assert node is not None
        assert {child.path.name for child in node.children} == {"poster.jpg", "sample.jpg", "emptysub"}

    def test_tree_has_single_node_per_path(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "extrafanart" / "1.jpg")

        inventory = _scan(lib)
        root = build_inventory_tree(inventory)

        paths = [child.path for child in root.children]
        assert paths == sorted(set(paths))
        node = find_inventory_node(root, lib / "old")
        assert node is not None
        assert node.reason is InventoryReason.ORPHAN

    def test_coverage_reflects_what_will_remain(self, tmp_path: Path) -> None:
        """覆盖信息按子树的真实结论算.

        残留条目的父目录里必然还有别的内容 (否则父目录自己就是候选), 因此它不会被预告清空;
        只含黑名单文件的目录确实会空.
        """
        lib = tmp_path / "lib"
        _touch(lib / "with_entry" / "ad-1.mkv")

        inventory = _scan(lib, blacklist=["ad-"])

        assert _reasons(inventory, lib) == {"with_entry/ad-1.mkv": InventoryReason.BLACKLIST}
        assert inventory.dirs[lib / "with_entry"].will_be_empty is True

    def test_orphan_entry_parent_is_not_marked_will_be_empty(self, tmp_path: Path) -> None:
        """外层目录本身就是候选, 覆盖信息不预告它将被清空."""
        lib = tmp_path / "lib"
        _touch(lib / "work" / "old" / "poster.jpg")

        inventory = _scan(lib)

        assert "work/old" in _reasons(inventory, lib)
        assert lib / "work" not in inventory.dirs or inventory.dirs[lib / "work"].will_be_empty is False

    def test_truncation_keeps_container_when_content_dropped(self, tmp_path: Path) -> None:
        """触顶时容器条目仍登记, 内容条目被丢弃并计数: 面板不会漏掉这处残留."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib, limit=1)
        node = find_inventory_node(build_inventory_tree(inventory), lib / "old")

        assert inventory.truncated is True
        assert inventory.dropped == 1
        assert [entry.path.name for entry in inventory.entries] == ["old"]
        assert node is not None
        assert node.expandable is True


class TestScopeLayer:
    def test_root_companion_files_are_registered_individually(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-039.nfo", size=7)
        _touch(lib / "poster.jpg", size=3)

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {
            "NSFS-039.nfo": InventoryReason.ORPHAN,
            "poster.jpg": InventoryReason.ORPHAN,
        }
        assert _sizes(inventory, lib) == {"NSFS-039.nfo": 7, "poster.jpg": 3}

    def test_root_companion_files_skipped_when_root_has_media(self, tmp_path: Path) -> None:
        """正片旁边活着的 NFO 是它的附属文件, 不是残留."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-001.mp4")
        _touch(lib / "NSFS-001.nfo")

        assert _reasons(_scan(lib), lib) == {}

    def test_root_files_and_subdir_candidates_coexist(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-039.nfo")
        _touch(lib / "old" / "poster.jpg")

        assert _reasons(_scan(lib), lib) == {
            "NSFS-039.nfo": InventoryReason.ORPHAN,
            "old": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
        }

    def test_root_symlink_entry_is_marked(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        target = tmp_path / "outside.nfo"
        target.write_bytes(b"x" * 5)
        lib.mkdir(parents=True)
        (lib / "link.nfo").symlink_to(target)

        inventory = _scan(lib)

        assert [entry.kind for entry in inventory.entries] == [InventoryEntryKind.SYMLINK]

    def test_subdir_scope_files_are_registered_individually(self, tmp_path: Path) -> None:
        """扫描范围目录永不登记为条目, 但它的直接子项按文件登记."""
        lib = tmp_path / "lib"
        scope = lib / "old"
        _touch(scope / "poster.jpg")
        _touch(scope / "NSFS-039.nfo")

        inventory = _scan(lib, scope=scope)

        assert _reasons(inventory, lib) == {
            "old/NSFS-039.nfo": InventoryReason.ORPHAN,
            "old/poster.jpg": InventoryReason.ORPHAN,
        }

    def test_scope_with_media_skips_its_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        scope = lib / "old"
        _touch(scope / "NSFS-039.mp4")
        _touch(scope / "NSFS-039.nfo")

        assert _reasons(_scan(lib, scope=scope), lib) == {}

    def test_root_files_respect_cooldown(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "poster.jpg", mtime=_NOW - 60)
        os.utime(lib, (_NOW - 60, _NOW - 60))

        assert _reasons(_scan(lib), lib) == {}

    def test_root_files_registered_when_media_elsewhere(self, tmp_path: Path) -> None:
        """库内别处有正片与库根这一层的文件是不是残留无关: 门槛只按本层算."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-039.nfo")
        _touch(lib / "work" / "NSFS-001.mp4")

        assert _reasons(_scan(lib), lib) == {"NSFS-039.nfo": InventoryReason.ORPHAN}

    def test_root_files_registered_with_trash_present(self, tmp_path: Path) -> None:
        """库根的回收站目录不拦逐文件登记: 这一层不会被整层删除, 回收站不受牵连."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-039.nfo")
        (lib / TRASH_DIRNAME).mkdir(parents=True)

        assert _reasons(_scan(lib), lib) == {"NSFS-039.nfo": InventoryReason.ORPHAN}

    def test_root_files_skipped_when_download_in_progress(self, tmp_path: Path) -> None:
        """本层有下载进度时不登记: 附属文件可能属于那个还没落地的下载."""
        lib = tmp_path / "lib"
        _touch(lib / "NSFS-039.nfo")
        _touch(lib / "NSFS-039.mp4.part")

        assert _reasons(_scan(lib), lib) == {}

    def test_junk_video_does_not_count_as_media(self, tmp_path: Path) -> None:
        """`._x.mp4` 是伴生文件, 命中黑名单也只是条目: 它不把同一层变成「有媒体」."""
        lib = tmp_path / "lib"
        _touch(lib / "._NSFS-039.mp4", size=1)
        _touch(lib / "NSFS-039.nfo")

        inventory = _scan(lib, blacklist=["_"])

        assert _reasons(inventory, lib) == {
            "._NSFS-039.mp4": InventoryReason.BLACKLIST,
            "NSFS-039.nfo": InventoryReason.ORPHAN,
        }


class TestBlockedUnexplained:
    """只暴露用户能据此行动的那一种原因: 目录里有无法识别的文件."""

    def test_unexplained_file_is_counted(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "notes.txt")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.unexplained == 1

    def test_nested_candidates_are_counted_once(self, tmp_path: Path) -> None:
        """嵌套的候选只按最外层那一个计数: 里外各计一次会让面板提示的数量虚高."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "notes.txt")
        _touch(lib / "old" / "sub" / "notes2.txt")

        inventory = _scan(lib)

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.unexplained == 1

    def test_media_and_veto_are_not_counted(self, tmp_path: Path) -> None:
        """目录里有媒体或不可删除的子项时与正常媒体目录一样, 不进任何计数."""
        lib = tmp_path / "lib"
        _touch(lib / "with_media" / "NSFS-001.mp4")
        _touch(lib / "with_media" / "NSFS-001.nfo")
        _touch(lib / "with_veto" / "poster.jpg")
        _touch(lib / "with_veto" / "NSFS-002.part")

        inventory = _scan(lib)

        assert inventory.blocked.total == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows 无 POSIX 权限位")
    def test_skipped_is_separate_from_blocked(self, tmp_path: Path) -> None:
        """读不到的目录进 `skipped_*`, 白名单外的文件进 `blocked`: 两种原因分开计数."""
        if os.geteuid() == 0:  # pragma: no cover - root 无视权限位
            pytest.skip("root 可以读任意目录")
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "notes.txt")
        unreadable = lib / "blocked"
        unreadable.mkdir(parents=True)
        unreadable.chmod(0)
        try:
            inventory = _scan(lib)
        finally:
            unreadable.chmod(0o755)

        assert inventory.skipped_dirs == 1
        assert inventory.skipped_files == 0
        assert inventory.blocked.unexplained == 1


class TestContainerNotCounted:
    """容器条目不是删除目标: 面板按它的子项算选中量, 它自己不能被算成一项."""

    def test_root_count_excludes_container(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")

        inventory = _scan(lib)
        root = build_inventory_tree(inventory)

        # 只有 1 个文件会被删: 容器条目代表它整棵子树, 不能与它的内容各算一次.
        assert root.entry_count == 1

    def test_container_count_sums_its_content(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        _touch(lib / "old" / "poster.jpg")
        _touch(lib / "old" / "NSFS-039.nfo")

        inventory = _scan(lib)
        node = find_inventory_node(build_inventory_tree(inventory), lib / "old")

        assert node is not None
        assert node.entry_count == 2

    def test_junk_only_dir_counts_one(self, tmp_path: Path) -> None:
        """只含垃圾文件的目录算 1 项: 取消它之后就没有可提交的内容."""
        lib = tmp_path / "lib"
        _touch(lib / "old" / ".DS_Store")

        inventory = _scan(lib)

        assert build_inventory_tree(inventory).entry_count == 1


class TestSurrogateDirectory:
    """目录链接 (Windows 的 junction 与挂载点): 不进去, 里面的内容不算库内条目."""

    def test_surrogate_content_is_not_collected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Windows 没有 junction, 用替代谓词验证同一分支."""
        lib = tmp_path / "lib"
        _touch(lib / "keep" / "poster.jpg")
        _touch(lib / "keep" / "linked" / "ad.mp4")
        monkeypatch.setattr(
            inventory_module,
            "is_name_surrogate",
            lambda st: st.st_ino == os.lstat(lib / "keep" / "linked").st_ino,
        )

        inventory = _scan(lib, blacklist=["ad"])

        assert _reasons(inventory, lib) == {}
        assert inventory.blocked.unexplained == 1

    def test_surrogate_at_root_is_not_traversed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """库根下的目录链接同样不进去: 库外内容一条也不进清单."""
        lib = tmp_path / "lib"
        _touch(lib / "linked" / "ad.mp4")
        monkeypatch.setattr(
            inventory_module, "is_name_surrogate", lambda st: st.st_ino == os.lstat(lib / "linked").st_ino
        )

        inventory = _scan(lib, blacklist=["ad"])

        assert inventory.entries == []
        assert inventory.blocked.total == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="junction 是 Windows 专有")
    def test_junction_content_is_not_collected(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        outside = tmp_path / "outside"
        (lib / "keep").mkdir(parents=True)
        outside.mkdir()
        _touch(outside / "ad.mp4")
        _touch(lib / "keep" / "poster.jpg")
        make_junction(lib / "keep" / "linked", outside)

        inventory = _scan(lib, blacklist=["ad"])

        assert _reasons(inventory, lib) == {}
