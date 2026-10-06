"""/libraries/{id}/cleanup/plan 只读接口: 面板状态、范围与按需下钻."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType
from amane.handlers import ScanInvalidPayload
from amane.library import PlanSource, scan_plan

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


async def _library(client: AsyncClient, root: Path, **extra: object) -> int:
    root.mkdir(parents=True, exist_ok=True)
    created = await client.post("libraries", json={"path": str(root), "scan": False, **extra})
    assert created.status_code == 201
    return int(created.json()["id"])


def _store_plan(app: FastAPI, root: Path, library_id: int, **kwargs: object):
    from amane.library import LibraryScan

    plan = scan_plan.sync(
        root,
        library_id=library_id,
        library_root=root,
        recursive=True,
        patterns=[],
        scan=LibraryScan(**kwargs),  # type: ignore[arg-type]
    )
    app.state.runtime.plan_store.put(plan)
    return plan


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_absent(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/plan")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["nodes"] == []
    assert body["scan_running"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_tree_nodes(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad-"])
    (root / "work").mkdir(parents=True)
    (root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (root / "empty").mkdir()
    _store_plan(app, root, library_id, blacklist_patterns=["ad-"])

    resp = await client.get(f"libraries/{library_id}/cleanup/plan")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["entry_count"] == 2
    assert body["entry_bytes"] == 10
    assert body["scope_path"] is None
    assert body["scan_running"] is False
    top = {node["name"]: node for node in body["nodes"]}
    assert set(top) == {"work", "empty"}
    assert top["work"]["has_children"] is True
    assert top["work"]["children"] is None
    assert top["work"]["will_be_empty"] is True
    assert top["empty"]["kind"] == "dir"
    assert top["empty"]["reason"] == "empty_dir"

    nested = await client.get(f"libraries/{library_id}/cleanup/plan/nodes", params={"path": "work"})

    assert nested.status_code == 200
    nodes = nested.json()["nodes"]
    assert [node["name"] for node in nodes] == ["ad-1.mkv"]
    assert nodes[0]["reason"] == "blacklist"
    assert nodes[0]["path"] == "work/ad-1.mkv"


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_nodes_unknown_path(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    root = safe_path / "lib"
    library_id = await _library(client, root)
    (root / "ad.mkv").write_bytes(b"x")
    _store_plan(app, root, library_id, blacklist_patterns=["ad"])

    resp = await client.get(f"libraries/{library_id}/cleanup/plan/nodes", params={"path": "gone"})

    assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_nodes_without_plan(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/plan/nodes")

    assert resp.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_running_flag(
    client: AsyncClient, repo: Repository, app: FastAPI, safe_path: Path, stop_worker: None
) -> None:
    """面板据此避免重复触发扫描; worker 已停, 排队的任务不会被认领."""
    root = safe_path / "lib"
    library_id = await _library(client, root)
    await repo.create_task(TaskType.SCAN_INVALID, ScanInvalidPayload(library_id=library_id, path=str(root)))

    resp = await client.get(f"libraries/{library_id}/cleanup/plan")

    assert resp.json()["scan_running"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_library_path_change_drops_plan(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    """库路径改掉后清单不再可用: 相对路径会按新库根重解释."""
    root = safe_path / "lib"
    moved = safe_path / "moved"
    library_id = await _library(client, root)
    (root / "ad.mkv").write_bytes(b"x")
    plan = _store_plan(app, root, library_id, blacklist_patterns=["ad"])
    moved.mkdir()

    updated = await client.patch(f"libraries/{library_id}", json={"path": str(moved)})

    assert updated.status_code == 200
    assert app.state.runtime.plan_store.get(plan.plan_id) is None
    assert app.state.runtime.plan_store.latest(library_id, PlanSource.RULES) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_scan_task_fills_panel_plan(client: AsyncClient, safe_path: Path) -> None:
    """提交扫描任务后, 面板能读到 worker 写进清单存放的清单 (注入必须共用同一实例)."""
    root = safe_path / "lib"
    library_id = await _library(client, root, blacklist_patterns=["ad"], min_file_size=1024)
    (root / "work").mkdir(parents=True)
    (root / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
    (root / "work" / "empty").mkdir()

    created = await client.post("tasks", json={"type": "scan_invalid", "library_id": library_id})
    assert created.status_code == 202
    task_id = created.json()["id"]
    for _ in range(100):
        task = await client.get(f"tasks/{task_id}")
        if task.json()["status"] in {"done", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert task.json()["status"] == "done"

    resp = await client.get(f"libraries/{library_id}/cleanup/plan")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["entry_count"] == 2
    names = {node["name"]: node for node in body["nodes"]}
    assert set(names) == {"work"}
    assert names["work"]["entry_count"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_trash_expansion_lists_history(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
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
    assert body["entry_count"] == 2
    assert body["entry_bytes"] == 30
    assert {node["name"] for node in body["nodes"]} == {"old-ad.mp4", "old-2.mp4"}
    assert all(node["outside"] is False for node in body["nodes"])
    plan = app.state.runtime.plan_store.get(body["plan_id"])
    assert plan is not None
    assert plan.source is PlanSource.EXPLICIT

    deleted = await client.post("tasks", json={"type": "delete", "library_id": library_id, "plan_id": body["plan_id"]})
    assert deleted.status_code == 202
    assert app.state.runtime.plan_store.get(body["plan_id"]) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_trash_expansion_without_trash(client: AsyncClient, safe_path: Path) -> None:
    library_id = await _library(client, safe_path / "lib")

    resp = await client.get(f"libraries/{library_id}/cleanup/trash")

    assert resp.status_code == 200
    assert resp.json()["exists"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_selection_expansion_previews_then_deletes(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
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
    assert body["entry_count"] == 3
    assert body["notices"] == []

    deleted = await client.post("tasks", json={"type": "delete", "library_id": library_id, "plan_id": body["plan_id"]})
    assert deleted.status_code == 202
    assert app.state.runtime.plan_store.get(body["plan_id"]) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_selection_rejects_foreign_media(client: AsyncClient, app: FastAPI, safe_path: Path) -> None:
    first = await _library(client, safe_path / "a")
    second = await _library(client, safe_path / "b")
    other = await app.state.runtime.repo.create_media_file(second, path=str(safe_path / "b" / "x.mp4"))

    resp = await client.post(f"libraries/{first}/cleanup/selection", json={"media_file_ids": [other.id]})

    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_plan_reports_last_scan_failure(
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

    resp = await client.get(f"libraries/{library_id}/cleanup/plan")

    body = resp.json()
    assert body["exists"] is False
    assert body["scan_running"] is False
    assert body["last_scan_error"] == "Not a directory: gone"
