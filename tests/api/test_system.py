"""/api/system: 桌面契约、重启、版本检查."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from amane.api.routes import API_PREFIX
from amane.api.routes.system import EXIT_RESTART
from amane.config import HotSettings
from amane.release import ReleaseSnapshot
from amane.server import EXIT_RESTART as SERVER_EXIT_RESTART
from tests.api.conftest import make_app

if TYPE_CHECKING:
    from pathlib import Path

    from httpx2 import AsyncClient as HttpxClient


@asynccontextmanager
async def _supervised_app(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    app = make_app(HotSettings(), tmp_path / "data", tmp_path / "logs", tmp_path / "files", supervised=True)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
            yield app, client
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio(loop_scope="function")
async def test_desktop_info(client: HttpxClient) -> None:
    resp = await client.get("system/desktop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"]
    assert data["data_dir"].endswith("data")
    assert data["supervised"] is False
    health = await client.get("health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "version" in health.json()


@pytest.mark.asyncio(loop_scope="function")
async def test_desktop_supervised_when_flag_set(tmp_path: Path) -> None:
    async with _supervised_app(tmp_path) as (_app, client):
        resp = await client.get("system/desktop")
        assert resp.status_code == 200
        assert resp.json()["supervised"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_restart_unavailable_without_supervisor(client: HttpxClient) -> None:
    resp = await client.post("system/restart")
    assert resp.status_code == 403
    assert "not available" in resp.json()["detail"]


@pytest.mark.asyncio(loop_scope="function")
async def test_restart_sets_exit_code_when_supervised(tmp_path: Path) -> None:
    assert EXIT_RESTART == SERVER_EXIT_RESTART == 3
    async with _supervised_app(tmp_path) as (app, client):
        resp = await client.post("system/restart")
        assert resp.status_code == 202
        assert app.state.exit_code == EXIT_RESTART


@pytest.mark.parametrize(
    ("snapshot", "expect"),
    [
        (ReleaseSnapshot(None, None), {"latest": None, "html_url": None, "newer": False}),
        (
            ReleaseSnapshot("v99.0.0", "https://github.com/sqzw-x/amane/releases/tag/v99.0.0"),
            {
                "latest": "v99.0.0",
                "html_url": "https://github.com/sqzw-x/amane/releases/tag/v99.0.0",
                "newer": True,
            },
        ),
    ],
    ids=["none", "newer"],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_check_release(
    client: HttpxClient,
    app: FastAPI,
    snapshot: ReleaseSnapshot,
    expect: dict[str, object],
) -> None:
    async def _fetch(*, proxy: str | None = None, url: str | None = None) -> ReleaseSnapshot:
        return snapshot

    app.state.runtime.release_checker.fetch = _fetch
    resp = await client.get("system/release")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current"]
    for key, value in expect.items():
        assert data[key] == value
