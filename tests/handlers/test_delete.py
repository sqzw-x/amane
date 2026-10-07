"""DELETE: 按清单执行删除, 不重新扫描, 不推导清单之外的路径."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import DeleteHandler, DeletePayload, ScanInvalidHandler, ScanInvalidPayload
from amane.handlers import delete as delete_module
from amane.library import InventoryStore

if TYPE_CHECKING:
    from amane.db.repository import Repository


async def _inventory_id(repo: Repository, store: InventoryStore, library_id: int, *, path: str | None = None) -> str:
    """跑一次 SCAN_INVALID 并返回清单标识; `path` 限定扫描范围."""
    payload = (
        ScanInvalidPayload(library_id=library_id)
        if path is None
        else ScanInvalidPayload(library_id=library_id, path=path)
    )
    result = await ScanInvalidHandler(repo, HotSettings(), store).handle(payload)
    assert result.success is True
    assert result.result is not None
    return result.result.inventory_id


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_executes_inventory(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    (lib_root / "work").mkdir(parents=True)
    ad = lib_root / "work" / "ad-1.mkv"
    ad.write_bytes(b"x" * 10)
    (lib_root / "empty").mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    row = await repo.create_media_file(lib.id, path=str(ad), number="AD-1")
    assert row.id is not None
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

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
    inventory = store.get(inventory_id)
    assert inventory is not None
    assert inventory.executed is True


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_missing_inventory_fails(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None

    result = await DeleteHandler(repo, InventoryStore()).handle(DeletePayload(library_id=lib.id, inventory_id="nope"))

    assert result.success is False
    assert "清单不存在" in (result.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_executed_inventory_refused(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    (lib_root / "ad.mkv").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = InventoryStore()
    inventory_id = await _inventory_id(repo, store, lib.id)
    handler = DeleteHandler(repo, store)
    first = await handler.handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))
    assert first.success is True

    second = await handler.handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

    assert second.success is False
    assert "已经执行过" in (second.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_spends_inventory_before_first_target(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消 / 崩溃落在第一个目标上时清单同样作废: 面板不能再拿半执行的快照重跑."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    ad = lib_root / "ad.mkv"
    ad.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = InventoryStore()
    inventory_id = await _inventory_id(repo, store, lib.id)

    async def _cancelled(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(delete_module, "delete_target", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

    # 一个目标都没删掉, 清单照样是花掉的.
    assert ad.exists()
    second = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))
    assert second.success is False
    assert "已经执行过" in (second.error or "")


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_preflight_failure_keeps_inventory(repo: Repository, tmp_path: Path) -> None:
    """预检失败一次都没动盘, 清单仍可执行: 否则一次误提交就要重扫整个库."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    ad = lib_root / "ad.mkv"
    ad.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad"])
    assert lib.id is not None
    store = InventoryStore()
    inventory_id = await _inventory_id(repo, store, lib.id)

    refused = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id + 1, inventory_id=inventory_id))
    assert refused.success is False

    second = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))
    assert second.success is True
    assert not ad.exists()


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
    store = InventoryStore()
    inventory_id = await _inventory_id(repo, store, lib.id)
    await repo.update_library(lib.id, path=str(moved_root))

    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

    assert result.success is False
    assert "库路径已变更" in (result.error or "")
    assert (moved_root / "ad.mkv").exists()
    assert (lib_root / "ad.mkv").exists()


@pytest.mark.parametrize(
    ("exclude", "include", "surviving"),
    [
        # 排除整个目录后单独纳入一个文件: 只删它.
        (["outer"], ["outer/inner/ad-1.mkv"], ["outer/inner/ad-2.mkv"]),
        # 纳入的是目录: 整棵子树重新进入删除集合.
        (["outer"], ["outer/inner"], []),
        # 纳入项内再排除一项 (三层): 最深的那条说了算.
        (["outer", "outer/inner/ad-1.mkv"], ["outer/inner"], ["outer/inner/ad-1.mkv"]),
        # 只有纳入项、没有排除项: 不影响执行集合.
        ([], ["outer/inner"], []),
    ],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_delete_include_restores_within_exclude(
    repo: Repository,
    tmp_path: Path,
    exclude: list[str],
    include: list[str],
    surviving: list[str],
) -> None:
    """纳入项在排除项内部把路径重新拉回删除集合; 两者互为祖先时按最深的一条判定."""
    lib_root = tmp_path / "lib"
    (lib_root / "outer" / "inner").mkdir(parents=True)
    first = lib_root / "outer" / "inner" / "ad-1.mkv"
    second = lib_root / "outer" / "inner" / "ad-2.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["ad-"])
    assert lib.id is not None
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id, exclude=exclude, include=include)
    )

    assert result.success is True
    assert result.result is not None
    for relative in surviving:
        assert (lib_root / relative).exists()
    for path in (first, second):
        assert path.exists() is (path.relative_to(lib_root).as_posix() in surviving)


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
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id, exclude=["Show A"])
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
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id, prune_empty_dirs=False)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.pruned_dirs == 0
    assert not ad.exists()
    assert (lib_root / "work").is_dir()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_other_library_inventory_refused(repo: Repository, tmp_path: Path) -> None:
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "ad.mkv").write_bytes(b"x")
    first = await repo.create_library(name="a", path=str(first_root), write_nfo=False, blacklist_patterns=["ad"])
    second = await repo.create_library(name="b", path=str(second_root), write_nfo=False)
    assert first.id is not None and second.id is not None
    store = InventoryStore()
    inventory_id = await _inventory_id(repo, store, first.id)

    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=second.id, inventory_id=inventory_id))

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
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id, path=str(lib_root / "a"))
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

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
    store = InventoryStore()

    inventory_id = await _inventory_id(repo, store, lib.id)
    result = await DeleteHandler(repo, store).handle(DeletePayload(library_id=lib.id, inventory_id=inventory_id))

    assert result.success is True
    assert result.result is not None
    assert result.result.indexed == 1
    assert await repo.get_media_file(row.id) is None


def _age_for_orphan(root: Path) -> None:
    """把库根整棵树 (目录与文件) 的 mtime 定到冷静期之外.

    库根自身的 mtime 也参与冷静期, 而写文件会把夹具目录的 mtime 留在写入那一刻, 于是判定会
    认为目录刚变动过. 文件定在两小时前, 目录定在 90 分钟前 — 目录必须比文件新.
    """
    now = time.time()
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (now - 7200, now - 7200))
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        os.utime(path, (now - 5400, now - 5400))
    os.utime(root, (now - 5400, now - 5400))


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_reverifies_orphan_dir_and_keeps_new_media(repo: Repository, tmp_path: Path) -> None:
    """扫描之后目录里落进了正片: 残留条目不再成立, 连同索引一起保留."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    (old / "NSFS-039.nfo").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    # 扫描之后才到达的正片.
    video = old / "NSFS-039.mp4"
    video.write_bytes(b"x")
    row = await repo.create_media_file(lib.id, path=str(video), number="NSFS-039")
    assert row.id is not None

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 1
    assert result.result.deleted == 0
    assert result.result.failed == 1
    assert video.exists()
    assert (old / "NSFS-039.nfo").exists()
    assert await repo.get_media_file(row.id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_reverify_accepts_junk_inside_orphan_dir(repo: Repository, tmp_path: Path) -> None:
    """垃圾文件也是条目 (面板默认折叠), 复验不能只认附属文件白名单."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    (old / "NSFS-039.nfo").write_bytes(b"x")
    junk = old / ".DS_Store"
    junk.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 0
    assert result.result.deleted == 2
    assert not junk.exists()
    assert not (old / "NSFS-039.nfo").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_reverifies_root_orphan_file(repo: Repository, tmp_path: Path) -> None:
    """根层文件条目同样复验: 该路径已经变成库会当作影片接收的路径时不再删除."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    entry = lib_root / "NSFS-039.nfo"
    entry.write_bytes(b"x")
    # 该库把 .nfo 当媒体收: 同名的正片落进来之后, 这条残留条目不再成立.
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, patterns=["**/*.nfo"])
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 1
    assert entry.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_orphan_file_gone_is_not_a_rejection(repo: Repository, tmp_path: Path) -> None:
    """条目路径已经不在磁盘上时按「已不存在」记账, 不算复验拒绝."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    nfo = lib_root / "NSFS-039.nfo"
    nfo.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    nfo.unlink()

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 0
    assert result.result.changed == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_orphan_still_executes_when_unchanged(repo: Repository, tmp_path: Path) -> None:
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    (old / "NSFS-039.nfo").write_bytes(b"x" * 5)
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 0
    assert result.result.deleted == 1
    assert not old.exists()
