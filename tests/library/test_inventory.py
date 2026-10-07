"""清理清单: 遍历产出、覆盖信息与进程内存放."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from amane.library import (
    INVENTORY_TTL_SECONDS,
    CleanupInventory,
    InventoryEntryKind,
    InventoryReason,
    InventorySource,
    InventoryStore,
    LibraryScan,
    build_inventory_tree,
    find_inventory_node,
    inventory_tree,
    scan_inventory,
    scan_trash,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _scan(*, blacklist: list[str] | None = None, min_size: int = 0, trailer: str | None = None) -> LibraryScan:
    return LibraryScan(blacklist_patterns=blacklist, min_file_size=min_size, trailer_pattern=trailer)


def _inventory(
    lib: Path,
    *,
    scan: LibraryScan | None = None,
    scope: Path | None = None,
    recursive: bool = True,
    limit: int = 20000,
    collect_media: bool = False,
) -> CleanupInventory:
    scope_dir = scope or lib
    return scan_inventory.sync(
        scope_dir,
        library_id=1,
        library_root=lib,
        recursive=recursive,
        patterns=[],
        scan=scan or _scan(),
        limit=limit,
        collect_media=collect_media,
    )


def _reasons(inventory: CleanupInventory, lib: Path) -> dict[str, InventoryReason]:
    """键用 POSIX 形式: 期望值里写的是 `/`, 而 Windows 的路径串用反斜杠."""
    return {entry.path.relative_to(lib).as_posix(): entry.reason for entry in inventory.entries}


class TestScanInventory:
    def test_classifies_unwanted_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
        (lib / "work" / "tiny.mp4").write_bytes(b"x")
        (lib / "work" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
        (lib / "work" / "trailer.mp4").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"], min_size=1024, trailer="trailer"))

        assert _reasons(inventory, lib) == {
            "work/ad-1.mkv": InventoryReason.BLACKLIST,
            "work/tiny.mp4": InventoryReason.UNDERSIZED,
        }
        assert inventory.truncated is False
        assert inventory.skipped_dirs == 0
        assert inventory.skipped_files == 0

    def test_trailer_is_never_undersized(self, tmp_path: Path) -> None:
        """预告片先于大小判定排除: 低码率预告片不是无效文件."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "trailer.mp4").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(min_size=1024, trailer="trailer"))

        assert inventory.entries == []

    def test_empty_directory_is_entry(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "empty").mkdir(parents=True)

        inventory = _inventory(lib)

        assert _reasons(inventory, lib) == {"empty": InventoryReason.EMPTY_DIR}
        entry = inventory.entries[0]
        assert entry.kind is InventoryEntryKind.DIR
        assert entry.size is None

    def test_coverage_propagates_upwards(self, tmp_path: Path) -> None:
        """子目录会空时父目录也算会消失: 与自底向上的剪枝一致."""
        lib = tmp_path / "lib"
        (lib / "a" / "b").mkdir(parents=True)
        (lib / "a" / "b" / "tiny.mp4").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(min_size=1024))

        assert inventory.dirs[lib / "a"].disk_children == 1
        assert inventory.dirs[lib / "a"].will_be_empty is True
        assert inventory.dirs[lib / "a" / "b"].will_be_empty is True

    def test_valid_media_keeps_directory(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "a").mkdir(parents=True)
        (lib / "a" / "tiny.mp4").write_bytes(b"x")
        (lib / "a" / "NSFS-039.mp4").write_bytes(b"x" * 4096)

        inventory = _inventory(lib, scan=_scan(min_size=1024))

        assert inventory.dirs[lib / "a"].disk_children == 2
        assert inventory.dirs[lib / "a"].will_be_empty is False

    def test_trash_subtree_excluded(self, tmp_path: Path) -> None:
        """回收站整棵不进清单, 且算作不可删除子项: 其父目录不会被预告清除."""
        lib = tmp_path / "lib"
        (lib / "work" / ".amane_trash").mkdir(parents=True)
        (lib / "work" / ".amane_trash" / "old.mkv").write_bytes(b"x")
        (lib / "work" / "ad.mkv").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]))

        assert _reasons(inventory, lib) == {"work/ad.mkv": InventoryReason.BLACKLIST}
        assert inventory.dirs[lib / "work"].disk_children == 2
        assert inventory.dirs[lib / "work"].will_be_empty is False

    def test_directory_holding_only_trash_is_not_empty(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work" / ".amane_trash").mkdir(parents=True)

        inventory = _inventory(lib)

        assert inventory.entries == []

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows 无 POSIX 权限位")
    def test_skips_unreadable_directory(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:  # pragma: no cover - root 无视权限位
            pytest.skip("root 可以读任意目录")
        lib = tmp_path / "lib"
        blocked = lib / "blocked"
        blocked.mkdir(parents=True)
        (blocked / "ad.mkv").write_bytes(b"x")
        blocked.chmod(0)
        try:
            inventory = _inventory(lib, scan=_scan(blacklist=["ad"]))
        finally:
            blocked.chmod(0o755)

        assert inventory.entries == []
        assert inventory.skipped_dirs == 1
        assert lib / "blocked" not in inventory.dirs

    def test_truncation_keeps_walking_for_media(self, tmp_path: Path, candidates_first_scandir: None) -> None:
        """触顶只丢条目: 本趟遍历同时给入库扫描收集媒体, 提前收工会让媒体缺项."""
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        for i in range(20):
            (lib / f"NSFS-{i:03d}.mp4").write_bytes(b"x" * 4096)
        for i in range(4):
            (lib / f"ad-{i}.mkv").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]), limit=2, collect_media=True)

        assert len(inventory.entries) == 2
        assert inventory.truncated is True
        assert len(inventory.media_hits) == 20

    def test_truncation_counts_every_dropped_candidate(self, tmp_path: Path, candidates_first_scandir: None) -> None:
        """未纳入的候选数要覆盖全部来源: 空目录同样是被丢弃的候选, 不能只数文件."""
        lib = tmp_path / "lib"
        lib.mkdir()
        for i in range(4):
            (lib / f"ad-{i}.mkv").write_bytes(b"x")
        for i in range(3):
            (lib / f"empty-{i}").mkdir()

        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]), limit=1)

        assert len(inventory.entries) == 1
        assert inventory.truncated is True
        assert inventory.dropped == 6

    def test_truncates_at_limit(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        for i in range(5):
            (lib / f"ad-{i}.mkv").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]), limit=2)

        assert len(inventory.entries) == 2
        assert inventory.truncated is True
        assert inventory.dropped == 3

    def test_non_recursive_ignores_subdirectories(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad.mkv").write_bytes(b"x")
        (lib / "ad-root.mkv").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]), recursive=False)

        assert _reasons(inventory, lib) == {"ad-root.mkv": InventoryReason.BLACKLIST}
        assert inventory.dirs == {}

    def test_scope_records_subdirectory(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad.mkv").write_bytes(b"x")
        (lib / "ad.mkv").write_bytes(b"x")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]), scope=lib / "work")

        assert inventory.scoped is True
        assert inventory.scope_path == lib / "work"
        assert _reasons(inventory, lib) == {"work/ad.mkv": InventoryReason.BLACKLIST}

    def test_broken_symlink_entry(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "ad.mkv").symlink_to(lib / "nowhere.mkv")

        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]))

        assert inventory.entries[0].kind is InventoryEntryKind.SYMLINK

    def test_total_size_dedupes_hardlinks(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        first = lib / "ad-1.mkv"
        second = lib / "ad-2.mkv"
        first.write_bytes(b"x" * 100)
        os.link(first, second)

        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]))

        assert len(inventory.entries) == 2
        assert inventory.total_size == 100


class TestInventoryStore:
    def _inventory(self, inventory_id: str, *, library_id: int = 1, created: datetime = _NOW) -> CleanupInventory:
        return CleanupInventory(
            inventory_id=inventory_id,
            library_id=library_id,
            root=Path("/lib"),
            scope_path=None,
            recursive=True,
            patterns=(),
            source=InventorySource.RULES,
            created_at=created,
        )

    def test_keeps_recent_window(self) -> None:
        store = InventoryStore(keep=2, now=lambda: _NOW)
        for inventory_id in ("a", "b", "c"):
            store.put(self._inventory(inventory_id))

        assert store.get("a") is None
        latest = store.latest(1, InventorySource.RULES)
        assert latest is not None
        assert latest.inventory_id == "c"

    def test_expired_inventory_is_absent(self) -> None:
        later = _NOW + timedelta(seconds=INVENTORY_TTL_SECONDS + 1)
        store = InventoryStore(now=lambda: later)
        store.put(self._inventory("a", created=_NOW))

        assert store.get("a") is None
        assert store.latest(1, InventorySource.RULES) is None

    def test_sources_are_separate(self) -> None:
        store = InventoryStore(now=lambda: _NOW)
        rules = self._inventory("rules")
        explicit = CleanupInventory(
            inventory_id="explicit",
            library_id=1,
            root=rules.root,
            scope_path=None,
            recursive=True,
            patterns=(),
            source=InventorySource.EXPLICIT,
            created_at=_NOW,
        )
        trash = CleanupInventory(
            inventory_id="trash",
            library_id=1,
            root=rules.root,
            scope_path=rules.root / ".amane_trash",
            recursive=True,
            patterns=(),
            source=InventorySource.TRASH,
            created_at=_NOW,
        )
        store.put(rules)
        store.put(explicit)
        store.put(trash)

        assert store.latest(1, InventorySource.RULES) is rules
        assert store.latest(1, InventorySource.EXPLICIT) is explicit
        assert store.latest(1, InventorySource.TRASH) is trash

    def test_latest_within_window(self) -> None:
        """``max_age`` 只接受刚产出过的那一份: 回收站展开据此复用同一秒级的重复请求."""
        store = InventoryStore(now=lambda: _NOW + timedelta(seconds=5))
        store.put(self._inventory("a", created=_NOW))

        assert store.latest(1, InventorySource.RULES, max_age=timedelta(seconds=10)) is not None
        assert store.latest(1, InventorySource.RULES, max_age=timedelta(seconds=2)) is None

    def test_drop_library(self) -> None:
        store = InventoryStore(now=lambda: _NOW)
        store.put(self._inventory("a", library_id=1))
        store.put(self._inventory("b", library_id=2))

        store.drop_library(1)

        assert store.get("a") is None
        assert store.get("b") is not None

    def test_executed_inventory_is_not_latest(self) -> None:
        """已执行的清单在面板侧等同于不存在; `get` 仍返回它, 供执行侧给出准确原因."""
        store = InventoryStore(now=lambda: _NOW)
        inventory = self._inventory("a")
        store.put(inventory)

        inventory.executed = True

        assert store.latest(1, InventorySource.RULES) is None
        assert store.get("a") is inventory


class TestInventoryTree:
    def test_aggregates_subtree_and_marks_empty(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "a" / "b").mkdir(parents=True)
        (lib / "a" / "b" / "ad-1.mkv").write_bytes(b"x" * 10)
        (lib / "a" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]))

        root = build_inventory_tree(inventory)

        assert root.entry_count == 1
        assert root.entry_bytes == 10
        node_a = find_inventory_node(root, lib / "a")
        assert node_a is not None
        assert node_a.entry_count == 1
        assert node_a.will_be_empty is False  # 正片还在
        node_b = find_inventory_node(root, lib / "a" / "b")
        assert node_b is not None
        assert node_b.will_be_empty is True
        assert [child.name for child in node_b.children] == ["ad-1.mkv"]
        assert node_b.children[0].reason is InventoryReason.BLACKLIST

    def test_hardlink_bytes_counted_once(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        first = lib / "ad-1.mkv"
        second = lib / "ad-2.mkv"
        first.write_bytes(b"x" * 100)
        os.link(first, second)
        inventory = _inventory(lib, scan=_scan(blacklist=["ad-"]))

        root = build_inventory_tree(inventory)

        assert root.entry_count == 2
        assert root.entry_bytes == 100
        assert all(child.hardlink for child in root.children)

    def test_empty_dir_entry_is_leaf_node(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "empty").mkdir(parents=True)
        inventory = _inventory(lib)

        root = build_inventory_tree(inventory)

        node = root.children[0]
        assert node.is_dir is True
        assert node.reason is InventoryReason.EMPTY_DIR
        assert node.will_be_empty is True
        assert node.children == ()

    def test_missing_node_returns_none(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "ad.mkv").write_bytes(b"x")
        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]))

        root = build_inventory_tree(inventory)

        assert find_inventory_node(root, lib / "gone") is None

    def test_inventory_tree_builds_once(self, tmp_path: Path) -> None:
        """面板翻页反复读同一份清单, 树构建一次后复用."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "ad.mkv").write_bytes(b"x")
        inventory = _inventory(lib, scan=_scan(blacklist=["ad"]))

        assert inventory_tree(inventory) is inventory_tree(inventory)
        assert inventory_tree(inventory).entry_count == 1


class TestScanTrash:
    def test_lists_everything_under_trash(self, tmp_path: Path) -> None:
        """回收站展开不做规则判定: 其下每个文件与空目录都是条目, 回收站目录自身不是."""
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        (trash / "sub").mkdir(parents=True)
        (trash / "old-ad.mp4").write_bytes(b"x" * 10)
        (trash / "sub" / "old-2.mp4").write_bytes(b"x" * 20)
        (trash / "sub" / "empty").mkdir()
        (lib / "keep.mp4").write_bytes(b"x" * 100)

        inventory = scan_trash.sync(trash, library_id=1, library_root=lib)

        assert inventory.source is InventorySource.TRASH
        assert inventory.scope_path == trash
        assert {entry.path.name: entry.reason for entry in inventory.entries} == {
            "old-ad.mp4": InventoryReason.EXPLICIT,
            "old-2.mp4": InventoryReason.EXPLICIT,
            "empty": InventoryReason.EXPLICIT,
        }
        assert inventory.total_size == 30

    def test_empty_trash_has_no_entries(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)

        inventory = scan_trash.sync(trash, library_id=1, library_root=lib)

        assert inventory.entries == []

    def test_truncation_reports_dropped(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)
        for index in range(3):
            (trash / f"old-{index}.mp4").write_bytes(b"x" * 10)

        inventory = scan_trash.sync(trash, library_id=1, library_root=lib, limit=1)

        assert len(inventory.entries) == 1
        assert inventory.truncated is True
        assert inventory.dropped == 2
