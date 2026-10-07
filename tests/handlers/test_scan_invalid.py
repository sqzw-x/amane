"""SCAN_INVALID: 只读扫描产出清单, 不改动磁盘与索引."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import ScanInvalidHandler, ScanInvalidPayload
from amane.library import InventoryReason, InventoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


def _handler(repo: Repository, store: InventoryStore) -> ScanInvalidHandler:
    return ScanInvalidHandler(repo, HotSettings(), store)


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
