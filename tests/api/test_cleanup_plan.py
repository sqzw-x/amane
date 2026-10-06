"""/libraries/{id}/cleanup/plan 只读接口: 面板状态、范围与按需下钻."""

from __future__ import annotations

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
