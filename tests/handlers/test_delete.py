"""DELETE: 按清单执行删除, 不重新扫描, 不推导清单之外的路径."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import DeleteHandler, DeletePayload, ScanInvalidHandler, ScanInvalidPayload
from amane.library import PlanStore

if TYPE_CHECKING:
    from amane.db.repository import Repository


async def _plan_id(repo: Repository, store: PlanStore, library_id: int, *, path: str | None = None) -> str:
    """跑一次 SCAN_INVALID 并返回清单标识; `path` 限定扫描范围."""
    payload = (
        ScanInvalidPayload(library_id=library_id)
        if path is None
        else ScanInvalidPayload(library_id=library_id, path=path)
    )
    result = await ScanInvalidHandler(repo, HotSettings(), store).handle(payload)
    assert result.success is True
    assert result.result is not None
    return result.result.plan_id


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_executes_plan(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    ad = lib_root / "work" / "ad-1.mkv"
    ad.write_bytes(b"x" * 10)
    (lib_root / "empty").mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    row = await repo.create_media_file(lib.id, path=str(ad), number="AD-1")
    assert row.id is not None
    store = PlanStore()

    plan_id = await _plan_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, plan_id=plan_id))

    assert result.success is True
    assert result.result is not None
    assert result.result.deleted == 2
    assert result.result.failed == 0
    assert result.result.freed_bytes == 10
    assert result.result.indexed == 1
    assert result.result.pruned_dirs == 1
    assert not ad.exists()
    assert not (lib_root / "empty").exists()
    assert await repo.get_media_file(row.id) is None
    plan = store.get(plan_id)
    assert plan is not None
    assert plan.executed is True


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_missing_plan_fails(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None

    result = await DeleteHandler(repo, PlanStore()).handle(DeletePayload(library_id=lib.id, plan_id="nope"))

    assert result.success is False
    assert "清单不存在" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_executed_plan_refused(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    (lib_root / "ad.mkv").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = PlanStore()
    plan_id = await _plan_id(repo, store, lib.id)
    handler = DeleteHandler(repo, store)
    first = await handler.handle(DeletePayload(library_id=lib.id, plan_id=plan_id))
    assert first.success is True

    second = await handler.handle(DeletePayload(library_id=lib.id, plan_id=plan_id))

    assert second.success is False
    assert "已经执行过" in (second.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_refuses_changed_library_root(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    moved_root = tmp_path / "moved"
    lib_root.mkdir()
    moved_root.mkdir()
    (lib_root / "ad.mkv").write_bytes(b"x")
    (moved_root / "ad.mkv").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = PlanStore()
    plan_id = await _plan_id(repo, store, lib.id)
    await repo.update_library(lib.id, path=str(moved_root))

    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, plan_id=plan_id))

    assert result.success is False
    assert "库路径已变更" in (result.error or "")
    assert (moved_root / "ad.mkv").exists()
    assert (lib_root / "ad.mkv").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_exclude_matches_path_components(repo: Repository, tmp_path: Path) -> None:
    """取消勾选 Show A 不应排除 Show A (2019): 按路径分量而不是字符串前缀."""
    lib_root = tmp_path / "lib"
    (lib_root / "Show A").mkdir(parents=True)
    (lib_root / "Show A (2019)").mkdir(parents=True)
    kept = lib_root / "Show A" / "ad-1.mkv"
    removed = lib_root / "Show A (2019)" / "ad-2.mkv"
    kept.write_bytes(b"x")
    removed.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    store = PlanStore()

    plan_id = await _plan_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(
        DeletePayload(library_id=lib.id, plan_id=plan_id, exclude=["Show A"])
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.excluded == 1
    assert kept.exists()
    assert not removed.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_without_prune_keeps_directory(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    ad = lib_root / "work" / "ad.mkv"
    ad.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = PlanStore()

    plan_id = await _plan_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(
        DeletePayload(library_id=lib.id, plan_id=plan_id, prune_empty_dirs=False)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.pruned_dirs == 0
    assert not ad.exists()
    assert (lib_root / "work").is_dir()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_other_library_plan_refused(repo: Repository, tmp_path: Path) -> None:
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "ad.mkv").write_bytes(b"x")
    first = await repo.create_library(name="a", path=str(first_root), write_nfo=False, blacklist_patterns=["ad"])
    second = await repo.create_library(name="b", path=str(second_root), write_nfo=False)
    assert first.id is not None and second.id is not None
    store = PlanStore()
    plan_id = await _plan_id(repo, store, first.id)

    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=second.id, plan_id=plan_id))

    assert result.success is False
    assert "不一致" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_prunes_ancestors_only(repo: Repository, tmp_path: Path) -> None:
    """自底向上剪枝本次删除项的祖先; 之前就存在的空目录不在范围内."""
    lib_root = tmp_path / "lib"
    (lib_root / "a" / "b").mkdir(parents=True)
    (lib_root / "a" / "b" / "ad.mkv").write_bytes(b"x")
    (lib_root / "preexisting").mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = PlanStore()

    plan_id = await _plan_id(repo, store, lib.id, path=str(lib_root / "a"))
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, plan_id=plan_id))

    assert result.success is True
    assert result.result is not None
    assert result.result.pruned_dirs == 2
    assert not (lib_root / "a").exists()
    assert (lib_root / "preexisting").is_dir()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_drops_index_by_directory_prefix(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "empty").mkdir(parents=True)
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    row = await repo.create_media_file(lib.id, path=str(lib_root / "empty" / "gone.mkv"), number="X-1")
    assert row.id is not None
    store = PlanStore()

    plan_id = await _plan_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, plan_id=plan_id))

    assert result.success is True
    assert result.result is not None
    assert result.result.indexed == 1
    assert await repo.get_media_file(row.id) is None
