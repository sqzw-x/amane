"""配置 API 端点的集成测试"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from tests.api.conftest import hot_for_tests

if TYPE_CHECKING:
    from httpx2 import AsyncClient


class TestConfigHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_patch_schema(self, client: AsyncClient):
        resp = await client.get("config")
        assert resp.status_code == 200
        assert HotSettings.model_validate(resp.json()) == hot_for_tests()
        data = resp.json()
        assert isinstance(data["scraping"]["download_resources"], list)
        assert isinstance(data["worker"]["concurrency"], int)
        assert isinstance(data["worker"]["poll_interval"], float)

        schema = await client.get("config/schema")
        assert schema.status_code == 200
        assert schema.json()["type"] == "object"
        assert "properties" in schema.json()

        empty = await client.patch("config", json={})
        assert empty.status_code == 200
        assert HotSettings.model_validate(empty.json()) == hot_for_tests()
        assert (await client.patch("config", json={"worker": {"concurrency": 999}})).status_code == 422

        patched = await client.patch(
            "config",
            json={
                "network": {"proxy": "http://new"},
                "worker": {"concurrency": 8},
                "scraping": {
                    "crop_poster": False,
                    "field_priority": {"actors": ["javdb", "theporndb"]},
                    "field_blacklist": {"title": ["javbus"]},
                },
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["worker"]["concurrency"] == 8
        assert body["scraping"]["crop_poster"] is False
        assert body["scraping"]["field_priority"]["actors"] == ["javdb", "theporndb"]
        assert body["scraping"]["field_blacklist"]["title"] == ["javbus"]

        await client.patch("config", json={"network": {"proxy": "socks5://test"}})
        got = await client.get("config")
        assert got.json()["network"]["proxy"] == "socks5://test"
        assert got.json()["worker"]["concurrency"] == 8


class TestConfigRebuild:
    """PUT /config 触发 runtime rebuild + event broadcast"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_triggers_worker_rebuild(self, client: AsyncClient, app):
        old_concurrency = app.state.runtime.worker._concurrency

        resp = await client.patch("config", json={"worker": {"concurrency": 3}})
        assert resp.status_code == 200
        assert resp.json()["worker"]["concurrency"] == 3

        new_concurrency = app.state.runtime.worker._concurrency
        assert new_concurrency == 3
        assert new_concurrency != old_concurrency

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_preserves_worker_functionality(self, client: AsyncClient, safe_path):
        scan_dir = safe_path / "after-rebuild"
        scan_dir.mkdir()

        await client.patch("config", json={"worker": {"concurrency": 5}})

        lib = (await client.post("libraries", json={"path": str(scan_dir)})).json()
        resp = await client.post("tasks", json={"type": "refresh", "library_id": lib["id"]})
        task_id = resp.json()["id"]

        for _ in range(40):
            check = await client.get(f"tasks/{task_id}")
            if check.json()["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)

        final = await client.get(f"tasks/{task_id}")
        assert final.json()["status"] == "done"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_r18_dsn_change_rebuilds_read_engine(self, client: AsyncClient, app):
        runtime = app.state.runtime
        assert runtime.r18_db is None

        resp = await client.patch(
            "config", json={"r18": {"dsn": "postgresql://u:p@127.0.0.1:5432/postgres", "db_name": "r18dev"}}
        )
        assert resp.status_code == 200

        assert runtime.r18_db is not None
        crawler = await runtime.factory.get("r18dev")
        assert crawler is not None
        assert crawler._db is runtime.r18_db
        assert runtime._old_r18_db is None
