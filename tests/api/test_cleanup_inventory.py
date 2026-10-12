"""/libraries/{id}/cleanup/inventory 只读接口: 面板状态、范围与按需下钻."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.api.routes.cleanup import MAX_NODE_PAGE_SIZE, TRASH_REUSE_WINDOW
from amane.db.models import TaskType
from amane.handlers import DeleteHandler, DeletePayload, ScanInvalidPayload
from amane.library import ORPHAN_COOLDOWN_SECONDS, InventorySource, OrphanScan, scan_inventory
from tests.helpers import LOAD_TOLERANT_TIMEOUT, await_for

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


async def _library(client: AsyncClient, root: Path, **extra: object) -> int:
    root.mkdir(parents=True, exist_ok=True)
    created = await client.post("libraries", json={"path": str(root), "scan": False, **extra})
    assert created.status_code == 201
    return int(created.json()["id"])


def _orphan_scan(root: Path) -> OrphanScan:
    return OrphanScan.from_library(
        library_root=root,
        scope_dir=root,
        subtitle_extensions=(),
        trailer_pattern=None,
        patterns=[],
        media_extensions=None,
    )


def _store_inventory(
    app: FastAPI,
    root: Path,
    library_id: int,
    *,
    recursive: bool = True,
    patterns: list[str] | None = None,
    limit: int = 20000,
    orphan_scan: OrphanScan | None = None,
    now: float | None = None,
    **kwargs: object,
):
    from amane.library import LibraryScan

    inventory = scan_inventory.sync(
        root,
        library_id=library_id,
        library_root=root,
        recursive=recursive,
        patterns=patterns or [],
        scan=LibraryScan(**kwargs),  # type: ignore[arg-type]
        limit=limit,
        orphan_scan=orphan_scan,
        now=now,
    )
    app.state.runtime.inventory_store.put(inventory)
    return inventory


async def _nodes(client: AsyncClient, library_id: int, **params: str | int) -> dict:
    resp = await client.get(f"libraries/{library_id}/cleanup/inventory/nodes", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_absent(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["inventory_id"] is None
    assert body["scan_running"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_tree_nodes(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad-"])
    (root / "work").mkdir(parents=True)
    (root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (root / "empty").mkdir()
    _store_inventory(app, root, library_id, blacklist_patterns=["ad-"])

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["scope_path"] is None
    assert body["scan_running"] is False

    page = await _nodes(client, library_id)

    assert page["path"] == ""
    assert page["total"] == 2
    assert page["entry_count"] == 2
    assert page["entry_bytes"] == 10
    top = {node["name"]: node for node in page["items"]}
    assert set(top) == {"work", "empty"}
    assert top["work"]["has_children"] is True
    assert top["work"]["children"] is None
    assert top["work"]["will_be_empty"] is True
    assert top["empty"]["kind"] == "dir"
    assert top["empty"]["reason"] == "empty_dir"

    nested = await _nodes(client, library_id, path="work")

    assert nested["path"] == "work"
    assert nested["total"] == 1
    assert nested["entry_count"] == 1
    nodes = nested["items"]
    assert [node["name"] for node in nodes] == ["ad-1.mkv"]
    assert nodes[0]["reason"] == "blacklist"
    assert nodes[0]["path"] == (Path("work") / "ad-1.mkv").as_posix()


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_nodes_paginate(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """一次下钻只给一页: 两万条候选整份下发会拖垮面板."""
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad-"])
    root.mkdir(parents=True, exist_ok=True)
    for index in range(5):
        (root / f"ad-{index}.mkv").write_bytes(b"x")
    _store_inventory(app, root, library_id, blacklist_patterns=["ad-"])

    first = await _nodes(client, library_id, limit=2)

    assert first["total"] == 5
    assert first["offset"] == 0
    assert first["limit"] == 2
    assert len(first["items"]) == 2

    second = await _nodes(client, library_id, limit=2, offset=2)

    assert [node["path"] for node in second["items"]] == ["ad-2.mkv", "ad-3.mkv"]

    beyond = await _nodes(client, library_id, limit=2, offset=4)

    assert len(beyond["items"]) == 1
    assert beyond["total"] == 5

    empty = await _nodes(client, library_id, offset=99)

    assert empty["items"] == []
    assert empty["total"] == 5

    for params in ({"limit": 0}, {"limit": MAX_NODE_PAGE_SIZE + 1}, {"offset": -1}):
        rejected = await client.get(f"libraries/{library_id}/cleanup/inventory/nodes", params=params)
        assert rejected.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_nodes_unknown_path(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    root = safe_path / "lib"
    library_id = await _library(client, root)
    (root / "ad.mkv").write_bytes(b"x")
    _store_inventory(app, root, library_id, blacklist_patterns=["ad"])

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory/nodes", params={"path": "gone"})

    assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_nodes_without_inventory(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory/nodes")

    assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_reports_truncation(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """触顶要在面板可见, 并说明还有多少候选没纳入."""
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad-"])
    for index in range(3):
        (root / f"ad-{index}.mkv").write_bytes(b"x")
    _store_inventory(app, root, library_id, blacklist_patterns=["ad-"], limit=1)

    body = (await client.get(f"libraries/{library_id}/cleanup/inventory")).json()

    assert body["exists"] is True
    assert body["truncated"] is True
    assert body["dropped"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_hidden_when_scan_settings_differ(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """遍历设置与当前库配置不一致的清单不算整库清单: 它只覆盖了库的一部分, 面板不该照整库渲染.

    四种不一致各建一个库, 在同一次 lifespan 内循环 (client 是函数级夹具, 不能按参数乘).
    """
    cases: list[tuple[bool, bool, list[str] | None, list[str] | None]] = [
        (False, True, None, None),
        (True, False, None, None),
        (True, True, ["*.mp4"], None),
        (True, True, None, ["*.mp4"]),
    ]
    for index, (library_recursive, inventory_recursive, library_patterns, inventory_patterns) in enumerate(cases):
        root = safe_path / f"lib-{index}"
        library_extra: dict[str, object] = {"recursive": library_recursive}
        if library_patterns is not None:
            library_extra["patterns"] = library_patterns
        library_id = await _library(client, root, blacklist_patterns=["ad-"], **library_extra)
        (root / "ad.mkv").write_bytes(b"x")
        _store_inventory(
            app,
            root,
            library_id,
            blacklist_patterns=["ad-"],
            recursive=inventory_recursive,
            patterns=inventory_patterns,
        )

        body = (await client.get(f"libraries/{library_id}/cleanup/inventory")).json()

        assert body["exists"] is False, cases[index]


@pytest.mark.asyncio(loop_scope="function")
async def test_executed_inventory_disappears_from_panel(
    client: AsyncClient, app: FastAPI, repo: Repository, safe_path: Path
) -> None:
    """删除成功后清单已花掉: 面板回到无清单态, 按标识读节点也按不存在处理."""
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad-"])
    (root / "ad.mkv").write_bytes(b"x")
    inventory = _store_inventory(app, root, library_id, blacklist_patterns=["ad-"])

    result = await DeleteHandler(repo, app.state.runtime.inventory_store).handle(
        DeletePayload(library_id=library_id, inventory_id=inventory.inventory_id)
    )

    assert result.success is True
    body = (await client.get(f"libraries/{library_id}/cleanup/inventory")).json()
    assert body["exists"] is False
    nodes = await client.get(
        f"libraries/{library_id}/cleanup/inventory/nodes", params={"inventory_id": inventory.inventory_id}
    )
    assert nodes.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_running_flag(
    client: AsyncClient, repo: Repository, app: FastAPI, safe_path: Path, stop_worker: None
) -> None:
    """面板据此避免重复触发扫描; worker 已停, 排队的任务不会被认领."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    await repo.create_task(TaskType.SCAN_INVALID, ScanInvalidPayload(library_id=library_id, path=str(root)))

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    assert resp.json()["scan_running"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_library_path_change_drops_inventory(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """库路径改掉后清单不再可用: 相对路径会按新库根重解释."""
    root = safe_path / "lib"
    moved = safe_path / "moved"
    library_id = await _library(client, root)
    (root / "ad.mkv").write_bytes(b"x")
    inventory = _store_inventory(app, root, library_id, blacklist_patterns=["ad"])
    moved.mkdir()

    updated = await client.patch(f"libraries/{library_id}", json={"path": str(moved)})

    assert updated.status_code == 200
    assert app.state.runtime.inventory_store.get(inventory.inventory_id) is None
    assert app.state.runtime.inventory_store.latest(library_id, InventorySource.RULES) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_task_fills_panel_inventory(client: AsyncClient, safe_path: Path) -> None:
    """提交扫描任务后, 面板能读到 worker 写进清单存放的清单 (注入必须共用同一实例)."""
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad"], min_file_size=1024)
    (root / "work").mkdir(parents=True)
    (root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (root / "work" / "empty").mkdir()

    created = await client.post("tasks", json={"type": "scan_invalid", "library_id": library_id})
    assert created.status_code == 202
    task_id = created.json()["id"]

    async def finished() -> str | None:
        task = await client.get(f"tasks/{task_id}")
        status = task.json()["status"]
        return status if status in {"done", "failed"} else None

    assert await await_for(finished, timeout=LOAD_TOLERANT_TIMEOUT) == "done"

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True

    page = await _nodes(client, library_id)

    assert page["entry_count"] == 2
    names = {node["name"]: node for node in page["items"]}
    assert set(names) == {"work"}
    assert names["work"]["entry_count"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_trash_expansion_lists_history(
    client: AsyncClient, app: FastAPI, safe_path: Path, stop_worker: None
) -> None:
    """回收站展开同步产出显式来源清单: 其下条目可删, 目录自身不可删."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    trash = root / ".amane_trash"
    trash.mkdir(parents=True)
    (trash / "old-ad.mp4").write_bytes(b"x" * 10)
    (trash / "old-2.mp4").write_bytes(b"x" * 20)

    resp = await client.get(f"libraries/{library_id}/cleanup/trash")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["path"] == ".amane_trash"
    inventory = app.state.runtime.inventory_store.get(body["inventory_id"])
    assert inventory is not None
    assert inventory.source is InventorySource.TRASH

    page = await _nodes(client, library_id, path=body["path"], inventory_id=body["inventory_id"])

    assert page["entry_count"] == 2
    assert page["entry_bytes"] == 30
    assert {node["name"] for node in page["items"]} == {"old-ad.mp4", "old-2.mp4"}

    deleted = await client.post(
        "tasks", json={"type": "delete", "library_id": library_id, "inventory_id": body["inventory_id"]}
    )

    assert deleted.status_code == 202
    # 提交只入队: 清单的消费发生在执行期, worker 已停, 因此它还没被花掉.
    assert inventory.executed is False


@pytest.mark.asyncio(loop_scope="function")
async def test_trash_expansion_without_trash(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/trash")

    assert resp.status_code == 200
    assert resp.json()["exists"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_trash_expansion_reuses_recent_inventory(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """窗口内的重复展开复用同一份; 窗口过后重新遍历, 面板才看得到刚删完的样子."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    trash = root / ".amane_trash"
    trash.mkdir(parents=True)
    (trash / "old-ad.mp4").write_bytes(b"x" * 10)

    first = await client.get(f"libraries/{library_id}/cleanup/trash")
    repeated = await client.get(f"libraries/{library_id}/cleanup/trash")

    assert repeated.json()["inventory_id"] == first.json()["inventory_id"]

    app.state.runtime.inventory_store.now = lambda: datetime.now(UTC) + TRASH_REUSE_WINDOW + timedelta(seconds=1)
    (trash / "old-ad.mp4").unlink()
    (trash / "old-2.mp4").write_bytes(b"x" * 20)

    rescanned = await client.get(f"libraries/{library_id}/cleanup/trash")

    assert rescanned.json()["inventory_id"] != first.json()["inventory_id"]
    page = await _nodes(
        client, library_id, path=rescanned.json()["path"], inventory_id=rescanned.json()["inventory_id"]
    )
    assert {node["name"] for node in page["items"]} == {"old-2.mp4"}


@pytest.mark.asyncio(loop_scope="function")
async def test_selection_expansion_previews_then_deletes(
    client: AsyncClient, app: FastAPI, safe_path: Path, stop_worker: None
) -> None:
    """文件表的删除入口: 展开预览 → 确认 → 删除任务, 只碰清单里的条目."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    work = root / "Studio" / "NSFS-039"
    work.mkdir(parents=True)
    video = work / "NSFS-039.mp4"
    video.write_bytes(b"v" * 100)
    (work / "NSFS-039.nfo").write_text("nfo")
    (work / "cover.jpg").write_bytes(b"cover")
    media = await app.state.runtime.repo.create_media_file(library_id, path=str(video), number="NSFS-039")
    assert media.id is not None

    preview = await client.post(
        f"libraries/{library_id}/cleanup/selection",
        json={"media_file_ids": [media.id], "include_work_dir": True},
    )

    assert preview.status_code == 200
    body = preview.json()
    assert body["exists"] is True
    assert body["notices"] == []

    page = await _nodes(client, library_id, inventory_id=body["inventory_id"])

    assert page["entry_count"] == 3
    assert [node["name"] for node in page["items"]] == ["Studio"]

    work_page = await _nodes(client, library_id, path="Studio/NSFS-039", inventory_id=body["inventory_id"])

    assert work_page["total"] == 3
    assert {node["name"] for node in work_page["items"]} == {"NSFS-039.mp4", "NSFS-039.nfo", "cover.jpg"}

    deleted = await client.post(
        "tasks", json={"type": "delete", "library_id": library_id, "inventory_id": body["inventory_id"]}
    )

    assert deleted.status_code == 202
    # 提交只入队: 清单的消费发生在执行期, worker 已停, 因此它还没被花掉.
    inventory = app.state.runtime.inventory_store.get(body["inventory_id"])
    assert inventory is not None
    assert inventory.executed is False


@pytest.mark.asyncio(loop_scope="function")
async def test_selection_notice_carries_code_and_relative_path(
    client: AsyncClient, app: FastAPI, safe_path: Path
) -> None:
    """未纳入的部分给码与参数 (文案由面板按界面语言给出), 路径与清单节点同一约定."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    work = root / "Studio" / "NSFS-039"
    work.mkdir(parents=True)
    for name in ("NSFS-039.mp4", "NSFS-040.mp4"):
        (work / name).write_bytes(b"v" * 100)

    repo = app.state.runtime.repo
    first = await repo.create_media_file(library_id, path=str(work / "NSFS-039.mp4"), number="NSFS-039")
    await repo.create_media_file(library_id, path=str(work / "NSFS-040.mp4"), number="NSFS-040")
    assert first.id is not None

    resp = await client.post(
        f"libraries/{library_id}/cleanup/selection",
        json={"media_file_ids": [first.id], "include_work_dir": True},
    )

    assert resp.status_code == 200
    assert resp.json()["notices"] == [
        {"kind": "work_dir_multiple", "path": "Studio/NSFS-039", "count": 2, "detail": None}
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_selection_rejects_foreign_media(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    first = await _library(client, safe_path / "a")
    second = await _library(client, safe_path / "b")
    other = await app.state.runtime.repo.create_media_file(second, path=str(safe_path / "b" / "x.mp4"))

    resp = await client.post(f"libraries/{first}/cleanup/selection", json={"media_file_ids": [other.id]})

    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_reports_last_scan_failure(
    client: AsyncClient,
    repo: Repository,
    safe_path: Path,
    stop_worker: None,
) -> None:
    """无清单时把最近一次扫描的失败原因带给面板, 否则用户只看到一个空状态."""
    library_id = await _library(client, safe_path / "lib")
    task = await repo.create_task(
        TaskType.SCAN_INVALID, ScanInvalidPayload(library_id=library_id, path=str(safe_path / "lib" / "gone"))
    )
    assert task.id is not None
    await repo.fail_task(task.id, error="Not a directory: gone")

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    body = resp.json()
    assert body["exists"] is False
    assert body["scan_running"] is False
    assert body["last_scan_error"] == "Not a directory: gone"


@pytest.mark.asyncio(loop_scope="function")
async def test_node_has_children_respects_noise_filter(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """折叠系统与同步工具的产物时, 只剩这类产物的行不报告可展开: 否则面板给出一个展开后为空的行."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    (root / "old").mkdir(parents=True)
    (root / "old" / ".DS_Store").write_bytes(b"x")
    _store_inventory(
        app, root, library_id, orphan_scan=_orphan_scan(root), now=time.time() + 2 * ORPHAN_COOLDOWN_SECONDS
    )

    page = await _nodes(client, library_id)

    top = {node["name"]: node for node in page["items"]}
    assert top["old"]["has_children"] is False

    shown = await _nodes(client, library_id, path="old", noise="true")

    assert shown["total"] == 1
    assert shown["items"][0]["name"] == ".DS_Store"


@pytest.mark.asyncio(loop_scope="function")
async def test_inventory_reports_blocked_dirs(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """未登记的候选目录在状态接口上可见: 面板据此提示「另有 N 个目录未纳入」."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    (root / "old").mkdir(parents=True)
    (root / "old" / "poster.jpg").write_bytes(b"x")
    (root / "old" / "notes.txt").write_bytes(b"x")
    _store_inventory(
        app, root, library_id, orphan_scan=_orphan_scan(root), now=time.time() + 2 * ORPHAN_COOLDOWN_SECONDS
    )

    resp = await client.get(f"libraries/{library_id}/cleanup/inventory")

    assert resp.status_code == 200
    assert resp.json()["blocked_dirs"] == 1
