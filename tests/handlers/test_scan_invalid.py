"""SCAN_INVALID: 只读扫描产出清单, 不改动磁盘与索引."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import RefreshHandler, RefreshPayload, ScanInvalidHandler, ScanInvalidPayload
from amane.library import ORPHAN_COOLDOWN_SECONDS, CleanupInventory, InventoryReason, InventorySource, InventoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


def _handler(repo: Repository, store: InventoryStore) -> ScanInvalidHandler:
    return ScanInvalidHandler(repo, HotSettings(), store)


def _age(root: Path) -> None:
    """把夹具整棵树的 mtime 定到冷却期之外: 两个 handler 都用真实时刻判定残留目录."""
    old = time.time() - 2 * ORPHAN_COOLDOWN_SECONDS
    for path in root.rglob("*"):
        os.utime(path, (old, old))
    os.utime(root, (old, old))


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_stores_inventory(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    (lib_root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (lib_root / "work" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
    (lib_root / "empty").mkdir()
    lib = await repo.create_library(
        name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"], min_file_size=1024
    )
    assert lib.id is not None
    store = InventoryStore()

    result = await _handler(repo, store).handle(ScanInvalidPayload(library_id=lib.id))

    assert result.success is True
    assert result.result is not None
    assert result.result.entries == 2
    assert result.result.truncated is False
    inventory = store.get(result.result.inventory_id)
    assert inventory is not None
    assert inventory.library_id == lib.id
    assert inventory.root == lib_root
    assert inventory.scoped is False
    assert {entry.path.name: entry.reason for entry in inventory.entries} == {
        "ad-1.mkv": InventoryReason.BLACKLIST,
        "empty": InventoryReason.EMPTY_DIR,
    }
    assert (lib_root / "work" / "ad-1.mkv").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_records_scope(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    (lib_root / "work" / "ad-1.mkv").write_bytes(b"x")
    (lib_root / "ad-root.mkv").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    store = InventoryStore()

    result = await _handler(repo, store).handle(ScanInvalidPayload(library_id=lib.id, path=str(lib_root / "work")))

    assert result.result is not None
    assert result.result.scope_path == str(lib_root / "work")
    inventory = store.get(result.result.inventory_id)
    assert inventory is not None
    assert inventory.scoped is True
    assert [entry.path.name for entry in inventory.entries] == ["ad-1.mkv"]


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_missing_library(repo: Repository) -> None:
    result = await _handler(repo, InventoryStore()).handle(ScanInvalidPayload(library_id=999))

    assert result.success is False
    assert "not found" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_missing_path(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None

    result = await _handler(repo, InventoryStore()).handle(
        ScanInvalidPayload(library_id=lib.id, path=str(lib_root / "gone"))
    )

    assert result.success is False
    assert "Not a directory" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_reports_blocked_dirs(repo: Repository, tmp_path: Path) -> None:
    """候选目录里有无法识别的文件时不登记, 但在结果里计数: 与「读不到」的跳过分开."""
    lib_root = tmp_path / "lib"
    (lib_root / "old").mkdir(parents=True)
    (lib_root / "old" / "poster.jpg").write_bytes(b"x")
    (lib_root / "old" / "notes.txt").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()

    result = await _handler(repo, store).handle(ScanInvalidPayload(library_id=lib.id))

    assert result.success is True
    assert result.result is not None
    assert result.result.entries == 0
    assert result.result.blocked_dirs == 1
    assert result.result.skipped_dirs == 0
    inventory = store.get(result.result.inventory_id)
    assert inventory is not None
    assert inventory.blocked.unexplained == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_refresh_and_scan_invalid_agree_on_entries(repo: Repository, tmp_path: Path) -> None:
    """两个产出方共用同一趟遍历: 同一棵树上产出的条目集合与原因必须一致.

    比较用库根相对路径: 两个 handler 的 `library_root` / `scope_dir` 来源不同, 分叉会出现在目录层.
    """
    lib_root = tmp_path / "lib"
    (lib_root / "old").mkdir(parents=True)
    (lib_root / "old" / "NSFS-039.nfo").write_bytes(b"x")
    (lib_root / "old" / "poster.jpg").write_bytes(b"x")
    (lib_root / "work").mkdir()
    (lib_root / "work" / "NSFS-001.mp4").write_bytes(b"x")
    (lib_root / "work" / "ad-1.mkv").write_bytes(b"x")
    (lib_root / "empty").mkdir()
    # 库根层的附属文件: 走 `_register_scope_files`, 两个产出方都要覆盖到.
    (lib_root / "NSFS-002.nfo").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    _age(lib_root)

    refresh_store = InventoryStore()
    refreshed = await RefreshHandler(repo, inventory_store=refresh_store).handle(
        RefreshPayload(library_id=lib.id, path=str(lib_root), scrape=set())
    )
    scan_store = InventoryStore()
    scanned = await _handler(repo, scan_store).handle(ScanInvalidPayload(library_id=lib.id))

    assert refreshed.success is True
    assert scanned.success is True
    assert scanned.result is not None
    refresh_inventory = refresh_store.latest(lib.id, InventorySource.RULES)
    scan_inventory = scan_store.get(scanned.result.inventory_id)
    assert refresh_inventory is not None
    assert scan_inventory is not None

    def entries(inventory: CleanupInventory) -> set[tuple[str, InventoryReason]]:
        return {(entry.path.relative_to(lib_root).as_posix(), entry.reason) for entry in inventory.entries}

    assert (
        entries(refresh_inventory)
        == entries(scan_inventory)
        == {
            ("NSFS-002.nfo", InventoryReason.ORPHAN),
            ("old", InventoryReason.ORPHAN),
            ("old/NSFS-039.nfo", InventoryReason.ORPHAN),
            ("old/poster.jpg", InventoryReason.ORPHAN),
            ("work/ad-1.mkv", InventoryReason.BLACKLIST),
            ("empty", InventoryReason.EMPTY_DIR),
        }
    )
