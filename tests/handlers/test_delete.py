"""DELETE: 按清单执行删除, 不重新扫描, 不推导清单之外的路径."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.handlers import DeleteHandler, DeletePayload, ScanInvalidHandler, ScanInvalidPayload
from amane.handlers import delete as delete_module
from amane.library import ORPHAN_COOLDOWN_SECONDS, InventoryStore, OrphanScan

if TYPE_CHECKING:
    from amane.db.repository import Repository


async def _inventory_id(repo: Repository, store: InventoryStore, library_id: int, *, path: str | None = None) -> str:
    """跑一次 SCAN_INVALID 并返回清单标识; `path` 限定扫描范围.

    与任务入口同一条路: 库默认值 (patterns / recursive) 由 ``resolve`` 落到 payload 上, 扫描侧
    与执行侧因此看到同一份设置.
    """
    payload = (
        ScanInvalidPayload(library_id=library_id)
        if path is None
        else ScanInvalidPayload(library_id=library_id, path=path)
    )
    await payload.resolve(repo)
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
        # 纳入项不匹配任何条目: 忽略, 执行集合不变.
        (["outer"], ["outer/gone"], ["outer/inner/ad-1.mkv", "outer/inner/ad-2.mkv"]),
        # 同一路径同时命中两组: 按纳入处理 (面板不会产出这两条, 这里只固定 API 侧行为).
        (["outer/inner"], ["outer/inner"], []),
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
    """把库根整棵树的 mtime 定到冷却期之外.

    目录自身的 mtime 也参与判定, 而创建夹具会把目录的 mtime 留在写入那一刻, 因此目录与文件
    都要调旧; utime 只改目标自身的时间, 不触碰父目录, 一趟即可.
    """
    old = time.time() - 2 * ORPHAN_COOLDOWN_SECONDS
    for path in root.rglob("*"):
        os.utime(path, (old, old))
    os.utime(root, (old, old))


def _orphan_scan(root: Path) -> OrphanScan:
    return OrphanScan.from_library(
        library_root=root,
        scope_dir=root,
        subtitle_extensions=[".srt"],
        trailer_pattern=None,
        patterns=[],
        media_extensions=None,
    )


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("layout", ["orphan", "empty"], ids=["残留目录", "空目录"])
async def test_delete_reverifies_target_that_gained_media(repo: Repository, tmp_path: Path, layout: str) -> None:
    """扫描之后目标处落进了正片: 条目不再成立, 连同索引一起保留.

    残留条目按宿主目录判, 空目录条目按目录是否仍为空判; 两条路径都拒绝同一个变异.
    """
    lib_root = tmp_path / "lib"
    target = lib_root / ("old" if layout == "orphan" else "empty")
    target.mkdir(parents=True)
    if layout == "orphan":
        (target / "NSFS-039.nfo").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    # 扫描之后才到达的正片.
    video = target / "NEW-001.mp4"
    video.write_bytes(b"x")
    row = await repo.create_media_file(lib.id, path=str(video), number="NEW-001")
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
    assert await repo.get_media_file(row.id) is not None
    if layout == "orphan":
        assert (target / "NSFS-039.nfo").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_reverify_scans_each_level_once(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一目录下的多个残留条目共用一次祖先探测: 重复列目录是网络盘上删除的主要开销."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    for name in ("a.nfo", "b.nfo", "c.nfo"):
        (old / name).write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    scanned: list[Path] = []
    original = delete_module._level_facts

    def _spy(directory: Path, *, orphan_scan: OrphanScan) -> delete_module._LevelFacts:
        scanned.append(directory)
        return original(directory, orphan_scan=orphan_scan)

    monkeypatch.setattr(delete_module, "_level_facts", _spy)
    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.deleted == 3
    # 每个文件各走一遍的话会有重复; 按目录记忆后 old 与库根各一次, 与目录顺序无关.
    assert sorted(path.name for path in scanned) == ["lib", "old"]
    assert len(scanned) == len(set(scanned))


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
    """根层条目同样复验: 库根这一层出现正片之后, 旁边的残留不再是残留."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    entry = lib_root / "NSFS-039.nfo"
    entry.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    # 扫描之后才到达的正片, 与残留条目同一层.
    (lib_root / "NEW-001.mp4").write_bytes(b"x")

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 1
    assert entry.exists()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("shape", ["file", "container", "empty"], ids=["文件被移走", "容器被移走", "空目录被删"])
async def test_delete_reverify_missing_target_is_changed(repo: Repository, tmp_path: Path, shape: str) -> None:
    """目标已经不在磁盘上时按「已不存在」记账, 不算复验拒绝 — 两者在结果里的含义不同."""
    lib_root = tmp_path / "lib"
    if shape == "file":
        lib_root.mkdir()
        target = lib_root / "NSFS-039.nfo"
        target.write_bytes(b"x")
    else:
        target = lib_root / ("old" if shape == "container" else "empty")
        target.mkdir(parents=True)
        if shape == "container":
            (target / "NSFS-039.nfo").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    if shape == "file":
        target.unlink()
    else:
        shutil.rmtree(target)

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 0
    assert result.result.failed == 0
    assert result.result.changed == 1


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("content", "deleted"),
    [
        (("NSFS-039.nfo",), 1),
        (("emptysub/", "poster.jpg"), 2),
    ],
    ids=["附属文件", "空子目录与附属文件"],
)
async def test_delete_removes_orphan_container_entries(
    repo: Repository, tmp_path: Path, content: tuple[str, ...], deleted: int
) -> None:
    """容器里的条目 (附属文件、保留的空子目录) 都是删除目标, 删完由剪枝回收容器目录."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    for relative in content:
        path = old / relative
        if relative.endswith("/"):
            path.mkdir(parents=True)
        else:
            path.write_bytes(b"x" * 5)
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
    assert result.result.deleted == deleted
    assert result.result.pruned_dirs == 1
    assert not old.exists()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("appeared", "is_dir"),
    [
        ("other/NEW-001.mp4", False),
        ("other/.stversions", True),
        ("other/notes.txt", False),
        ("NSFS-039.mp4.part", False),
    ],
    ids=["媒体", "不可删除的子项", "白名单外的文件", "下载进度"],
)
async def test_delete_reverifies_orphan_container_subtree(
    repo: Repository, tmp_path: Path, appeared: str, is_dir: bool
) -> None:
    """残留目录的子树里出现新的否决项时整棵子树不成立: 条目级复验看不到兄弟子目录."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    (old / "other").mkdir(parents=True)
    (old / "poster.jpg").write_bytes(b"x")
    (old / "other" / "1.jpg").write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    added = old / appeared
    if is_dir:
        added.mkdir()
    else:
        added.write_bytes(b"x")

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 2
    assert result.result.deleted == 0
    assert (old / "poster.jpg").exists()
    assert (old / "other" / "1.jpg").exists()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("appeared", "rejected", "deleted"),
    [("NEW-001.mp4", 0, 1), ("old/NSFS-039.mp4.part", 1, 0)],
    ids=["范围之上出现媒体", "本层出现下载进度"],
)
async def test_delete_reverify_stops_at_scan_scope(
    repo: Repository, tmp_path: Path, appeared: str, rejected: int, deleted: int
) -> None:
    """范围清单的复验只走到扫描范围那一层: 之上的媒体不否决, 本层的否决项拦住这一层的条目."""
    lib_root = tmp_path / "lib"
    scope = lib_root / "old"
    scope.mkdir(parents=True)
    entry = scope / "NSFS-039.nfo"
    entry.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id, path=str(scope))

    # 扫描之后才到达的媒体 / 下载进度.
    (lib_root / appeared).write_bytes(b"x")

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == rejected
    assert result.result.deleted == deleted
    assert entry.exists() is (deleted == 0)


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_reverify_ignores_cooldown(repo: Repository, tmp_path: Path) -> None:
    """复验不重复施加冷却期: 条目已经过用户确认, 扫描之后被触碰过的文件仍然删除."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    nfo = old / "NSFS-039.nfo"
    nfo.write_bytes(b"x")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    store = InventoryStore()
    _age_for_orphan(lib_root)
    inventory_id = await _inventory_id(repo, store, lib.id)

    # 扫描之后被重新写入: 扫描侧的冷却期此刻会否决这个目录.
    os.utime(nfo, None)

    result = await DeleteHandler(repo, store, HotSettings()).handle(
        DeletePayload(library_id=lib.id, inventory_id=inventory_id)
    )

    assert result.success is True
    assert result.result is not None
    assert result.result.reverify_rejected == 0
    assert result.result.deleted == 1
    assert not nfo.exists()


@pytest.mark.parametrize(
    ("name", "is_dir", "expected"),
    [
        ("NEW-001.mp4", False, "目录里出现了媒体: NEW-001.mp4"),
        ("sub/NEW-001.mp4", False, "目录里出现了媒体: NEW-001.mp4"),
        ("notes.txt", False, "目录里出现了无法解释的文件: notes.txt"),
        ("NSFS-039.mp4.part", False, "目录里出现了不可删除的子项: NSFS-039.mp4.part"),
        (".stversions", True, "目录里出现了不可删除的子项: .stversions"),
        # 垃圾项先于媒体判据: `._x.mp4` 是伴生文件, 不否决.
        ("._NSFS-039.mp4", False, None),
    ],
    ids=["媒体", "子目录里的媒体", "白名单外的文件", "下载进度", "不可删除的目录", "垃圾项"],
)
def test_reverify_subtree_reports_reason(tmp_path: Path, name: str, is_dir: bool, expected: str | None) -> None:
    """复验拒绝的文案是排障依据, 结果里只留计数: 文案区分原因, 由这张表固定."""
    container = tmp_path / "old"
    container.mkdir()
    appeared = container / name
    if is_dir:
        appeared.mkdir(parents=True)
    else:
        appeared.parent.mkdir(parents=True, exist_ok=True)
        appeared.write_bytes(b"x")

    assert delete_module._reverify_subtree(container, orphan_scan=_orphan_scan(tmp_path)) == expected


def test_reverify_empty_dir_reports_reason(tmp_path: Path) -> None:
    """空目录条目只按目录是否仍为空判定: 执行侧删目录是递归的."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert delete_module._reverify_empty_dir(empty) is None

    (empty / "NEW-001.mp4").write_bytes(b"x")

    assert delete_module._reverify_empty_dir(empty) == "目录不再是空的"


def test_reverify_subtree_rejects_surrogate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描之后才出现的目录替身 (Windows 的 junction): 子树里有没有媒体无从判断, 按拒绝处理."""
    container = tmp_path / "old"
    (container / "linked").mkdir(parents=True)
    monkeypatch.setattr(
        delete_module, "is_name_surrogate", lambda st: st.st_ino == os.lstat(container / "linked").st_ino
    )

    assert (
        delete_module._reverify_subtree(container, orphan_scan=_orphan_scan(tmp_path))
        == "目录里出现了指向别处的目录: linked"
    )


def test_media_probe_reports_ancestor_and_level_reason(tmp_path: Path) -> None:
    """库根与扫描范围层的拒绝文案: 与子树文案分开, 排障时能区分是哪一层."""
    lib_root = tmp_path / "lib"
    old = lib_root / "old"
    old.mkdir(parents=True)
    (old / "NSFS-039.nfo").write_bytes(b"x")
    (lib_root / "NSFS-001.mp4").write_bytes(b"x")
    orphan_scan = _orphan_scan(lib_root)

    assert delete_module._MediaProbe(orphan_scan=orphan_scan).ancestor_refusal(old) == "目录的祖先里出现了媒体"

    (old / "NSFS-039.mp4.part").write_bytes(b"x")

    assert delete_module._MediaProbe(orphan_scan=orphan_scan).level_refusal(old) == "本层出现了下载进度"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 无 POSIX 权限位")
@pytest.mark.parametrize(
    ("mode", "expected"),
    [(0o000, "目录无法读取"), (0o400, "子项无法读取")],
    ids=["目录无法读取", "子项无法读取"],
)
def test_reverify_subtree_reports_unreadable(tmp_path: Path, mode: int, expected: str) -> None:
    """读不到按拒绝处理, 与「有媒体」分开: 只缺搜索位时列得出来, 但 lstat 不了子项."""
    if os.geteuid() == 0:  # pragma: no cover - root 无视权限位
        pytest.skip("root 可以读任意目录")
    container = tmp_path / "old"
    container.mkdir()
    (container / "poster.jpg").write_bytes(b"x")
    blocked = container / "blocked"
    blocked.mkdir()
    (blocked / "inner.bin").write_bytes(b"x")
    blocked.chmod(mode)
    try:
        refusal = delete_module._reverify_subtree(container, orphan_scan=_orphan_scan(tmp_path))
    finally:
        blocked.chmod(0o755)

    assert refusal is not None
    assert refusal.startswith(expected)
