"""SCAN_INVALID: 只读扫描产出清单, 不改动磁盘与索引."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import ScanInvalidHandler, ScanInvalidPayload
from amane.library import PlanReason, PlanStore

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


def _handler(repo: Repository, store: PlanStore) -> ScanInvalidHandler:
    return ScanInvalidHandler(repo, HotSettings(), store)


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_stores_plan(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    (lib_root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (lib_root / "work" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
    (lib_root / "empty").mkdir()
    lib = await repo.create_library(
        name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"], min_file_size=1024
    )
    assert lib.id is not None
    store = PlanStore()

    result = await _handler(repo, store).handle(ScanInvalidPayload(library_id=lib.id))

    assert result.success is True
    assert result.result is not None
    assert result.result.entries == 2
    assert result.result.truncated is False
    plan = store.get(result.result.plan_id)
    assert plan is not None
    assert plan.library_id == lib.id
    assert plan.root == lib_root
    assert plan.scoped is False
    assert {entry.path.name: entry.reason for entry in plan.entries} == {
        "ad-1.mkv": PlanReason.BLACKLIST,
        "empty": PlanReason.EMPTY_DIR,
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
    store = PlanStore()

    result = await _handler(repo, store).handle(ScanInvalidPayload(library_id=lib.id, path=str(lib_root / "work")))

    assert result.result is not None
    assert result.result.scope_path == str(lib_root / "work")
    plan = store.get(result.result.plan_id)
    assert plan is not None
    assert plan.scoped is True
    assert [entry.path.name for entry in plan.entries] == ["ad-1.mkv"]


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_missing_library(repo: Repository) -> None:
    result = await _handler(repo, PlanStore()).handle(ScanInvalidPayload(library_id=999))

    assert result.success is False
    assert "not found" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_invalid_missing_path(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None

    result = await _handler(repo, PlanStore()).handle(
        ScanInvalidPayload(library_id=lib.id, path=str(lib_root / "gone"))
    )

    assert result.success is False
    assert "Not a directory" in (result.error or "")
