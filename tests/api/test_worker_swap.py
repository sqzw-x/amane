"""配置变更时的 worker 替换与退役行为 (经 FastAPI lifespan)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx2 import ASGITransport, AsyncClient

import amane.app.bootstrap as bootstrap_module
import amane.app.runtime as runtime_module
from amane.api.routes import API_PREFIX
from amane.config import HotSettings
from amane.db.models import TaskType
from amane.handlers.models import RefreshPayload, RefreshResult
from amane.handlers.protocol import TaskHandler, TaskResult
from amane.scheduler.worker import CANCEL_ERROR

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

    from amane.app.runtime import AppRuntime


@dataclass
class BlockingRefresh:
    """替换 REFRESH handler 为阻塞实现; 每一代记录构造期 HotSettings 与事件."""

    generations: list[tuple[HotSettings, asyncio.Event, asyncio.Event]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = runtime_module.build_handlers

        def wrapped(*args: Any, **kwargs: Any) -> dict[TaskType, TaskHandler[Any, Any]]:
            handlers = original(*args, **kwargs)
            hot: HotSettings = args[4]
            started = asyncio.Event()
            release = asyncio.Event()
            self.generations.append((hot, started, release))

            class BlockingHandler(TaskHandler[RefreshPayload, RefreshResult]):
                def __init__(self) -> None:
                    super().__init__(payload_t=RefreshPayload, result_t=RefreshResult)

                async def handle(self, payload: RefreshPayload) -> TaskResult[RefreshResult]:
                    started.set()
                    await release.wait()
                    return TaskResult(success=True)

            handlers[TaskType.REFRESH] = BlockingHandler()
            return handlers

        monkeypatch.setattr(runtime_module, "build_handlers", wrapped)
        monkeypatch.setattr(bootstrap_module, "build_handlers", wrapped)


@pytest_asyncio.fixture
async def swap_client(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, BlockingRefresh, AppRuntime]]:
    blocker = BlockingRefresh()
    blocker.install(monkeypatch)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
        yield client, blocker, app.state.runtime
    await ctx.__aexit__(None, None, None)


async def _create_library(client: AsyncClient, path: Path) -> int:
    resp = await client.post("libraries", json={"path": str(path)})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _submit_refresh(client: AsyncClient, library_id: int) -> int:
    resp = await client.post("tasks", json={"type": "refresh", "library_id": library_id})
    assert resp.status_code == 202, resp.text
    return int(resp.json()["id"])


async def _await_status(client: AsyncClient, task_id: int, status: str, timeout: float = 5.0) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            resp = await client.get(f"tasks/{task_id}")
            assert resp.status_code == 200
            body = resp.json()
            if body["status"] == status:
                return body
            await asyncio.sleep(0.02)


@pytest.mark.asyncio(loop_scope="function")
async def test_running_task_survives_config_change(
    swap_client: tuple[AsyncClient, BlockingRefresh, AppRuntime], safe_path: Path
) -> None:
    """配置变更不取消运行中任务; 新任务由新 worker 用新配置执行."""
    client, blocker, runtime = swap_client
    library_id = await _create_library(client, safe_path)

    first = await _submit_refresh(client, library_id)
    await asyncio.wait_for(blocker.generations[0][1].wait(), timeout=5)

    resp = await client.patch("config", json={"watermark": {"enabled": True}})
    assert resp.status_code == 200
    assert len(blocker.generations) == 2
    first_hot = blocker.generations[0][0]
    second_hot = blocker.generations[1][0]
    assert first_hot.watermark.enabled is False
    assert second_hot.watermark.enabled is True

    # 旧 worker 主循环已经退出 (不再认领), 但原任务仍在运行
    assert runtime._retiring, "旧 worker 未进入退役登记"
    old_worker = runtime._retiring[0].worker
    await asyncio.wait_for(old_worker.wait_stopped(), timeout=5)
    running = await _await_status(client, first, "running")
    assert running["status"] == "running"

    # 新任务由新 worker 认领, 归第二代 handler
    second = await _submit_refresh(client, library_id)
    await asyncio.wait_for(blocker.generations[1][1].wait(), timeout=5)

    # 只放行第二代: 第二个任务完成, 第一个仍在运行
    blocker.generations[1][2].set()
    await _await_status(client, second, "done")
    still_running = await client.get(f"tasks/{first}")
    assert still_running.json()["status"] == "running"

    blocker.generations[0][2].set()
    await _await_status(client, first, "done")


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_reaches_retiring_worker(
    swap_client: tuple[AsyncClient, BlockingRefresh, AppRuntime], safe_path: Path
) -> None:
    """退役 worker 的任务仍可经批量接口取消并收敛到终态."""
    client, blocker, runtime = swap_client
    library_id = await _create_library(client, safe_path)

    task_id = await _submit_refresh(client, library_id)
    await asyncio.wait_for(blocker.generations[0][1].wait(), timeout=5)

    resp = await client.patch("config", json={"watermark": {"enabled": True}})
    assert resp.status_code == 200
    old_worker = runtime._retiring[0].worker
    await asyncio.wait_for(old_worker.wait_stopped(), timeout=5)

    cancelled = await client.post(
        "tasks/batch", json={"action": "cancel", "task_ids": [task_id], "status": None, "type": None}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["affected"] == 1

    failed = await _await_status(client, task_id, "failed")
    assert failed["error"] == CANCEL_ERROR


@pytest.mark.asyncio(loop_scope="function")
async def test_stop_workers_cancels_running(
    swap_client: tuple[AsyncClient, BlockingRefresh, AppRuntime], safe_path: Path
) -> None:
    """关闭编排取消活跃任务并收敛到终态."""
    client, blocker, runtime = swap_client
    library_id = await _create_library(client, safe_path)

    task_id = await _submit_refresh(client, library_id)
    await asyncio.wait_for(blocker.generations[0][1].wait(), timeout=5)

    await asyncio.wait_for(runtime.stop_workers(closing=False), timeout=5)

    failed = await _await_status(client, task_id, "failed")
    assert failed["error"] == CANCEL_ERROR


@pytest.mark.asyncio(loop_scope="function")
async def test_r18_handle_released_after_retire(
    swap_client: tuple[AsyncClient, BlockingRefresh, AppRuntime],
) -> None:
    """r18 配置变更后旧句柄在旧 worker 释放后关闭, 新句柄指向当前引擎."""
    client, _blocker, runtime = swap_client
    first = await client.patch(
        "config", json={"r18": {"dsn": "postgresql://u:p@127.0.0.1:5432/postgres", "db_name": "r18a"}}
    )
    assert first.status_code == 200
    handle_a = runtime.r18_handle
    assert handle_a is not None
    assert handle_a.engine is runtime.r18_db

    second = await client.patch(
        "config", json={"r18": {"dsn": "postgresql://u:p@127.0.0.1:5432/postgres", "db_name": "r18b"}}
    )
    assert second.status_code == 200
    handle_b = runtime.r18_handle
    assert handle_b is not None and handle_b is not handle_a
    assert runtime.r18_db is handle_b.engine

    await asyncio.gather(*list(runtime._retire_tasks), return_exceptions=True)
    assert handle_a.closed is True
