"""配置 API 端点的集成测试"""

from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from tests.api.conftest import hot_for_tests

if TYPE_CHECKING:
    from httpx2 import AsyncClient


class TestGetConfig:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_full_config(self, client: AsyncClient):
        resp = await client.get("config")
        assert resp.status_code == 200
        assert HotSettings.model_validate(resp.json()) == hot_for_tests()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_response_types(self, client: AsyncClient):
        resp = await client.get("config")
        data = resp.json()
        assert isinstance(data["scraping"]["download_resources"], list)
        assert isinstance(data["worker"]["concurrency"], int)
        assert isinstance(data["worker"]["poll_interval"], float)


class TestUpdateConfig:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_update(self, client: AsyncClient):
        resp = await client.patch(
            "config",
            json={
                "network": {"proxy": "http://new"},
                "worker": {"concurrency": 8},
                "scraping": {"crop_poster": False, "field_priority": {"actors": ["javdb", "theporndb"]}},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker"]["concurrency"] == 8
        assert data["scraping"]["crop_poster"] is False
        assert data["scraping"]["field_priority"]["actors"] == ["javdb", "theporndb"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_value_returns_422(self, client: AsyncClient):
        resp = await client.patch("config", json={"worker": {"concurrency": 999}})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_patch_returns_current(self, client: AsyncClient):
        resp = await client.patch("config", json={})
        assert resp.status_code == 200
        assert HotSettings.model_validate(resp.json()) == hot_for_tests()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_reflects_previous_put(self, client: AsyncClient):
        await client.patch("config", json={"network": {"proxy": "socks5://test"}})
        resp = await client.get("config")
        expected = hot_for_tests()
        expected.network.proxy = "socks5://test"
        assert HotSettings.model_validate(resp.json()) == expected


class TestGetConfigSchema:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_json_schema(self, client: AsyncClient):
        resp = await client.get("config/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "object"
        assert "properties" in data


class TestConfigRebuild:
    """PUT /config 触发 runtime rebuild + event broadcast"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_triggers_worker_rebuild(self, client: AsyncClient, app):
        """修改 concurrency 后 worker 使用新值"""
        old_concurrency = app.state.runtime.worker._concurrency

        resp = await client.patch("config", json={"worker": {"concurrency": 3}})
        assert resp.status_code == 200
        assert resp.json()["worker"]["concurrency"] == 3

        new_concurrency = app.state.runtime.worker._concurrency
        assert new_concurrency == 3
        assert new_concurrency != old_concurrency

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_preserves_worker_functionality(self, client: AsyncClient, safe_path):
        """rebuild 后 worker 仍能正常处理任务"""
        scan_dir = safe_path / "after-rebuild"
        scan_dir.mkdir()

        # 触发 rebuild
        await client.patch("config", json={"worker": {"concurrency": 5}})

        # 提交任务, 验证 worker 仍能处理
        lib = (await client.post("libraries", json={"path": str(scan_dir)})).json()
        resp = await client.post("tasks", json={"type": "refresh", "library_id": lib["id"]})
        task_id = resp.json()["id"]

        import asyncio

        for _ in range(40):
            check = await client.get(f"tasks/{task_id}")
            if check.json()["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)

        final = await client.get(f"tasks/{task_id}")
        assert final.json()["status"] == "done"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_r18_dsn_change_rebuilds_read_engine(self, client: AsyncClient, app):
        """改 r18.dsn 后只读引擎被重建并注入工厂 (r18 在 Hot, 热生效)."""
        runtime = app.state.runtime
        assert runtime.r18_db is None  # 默认未配置

        resp = await client.patch(
            "config", json={"r18": {"dsn": "postgresql://u:p@127.0.0.1:5432/postgres", "db_name": "r18dev"}}
        )
        assert resp.status_code == 200

        # 引擎已建立 (构造是惰性的, 不实际连接), 且注入了新工厂
        assert runtime.r18_db is not None
        crawler = await runtime.factory.get("r18dev")
        assert crawler is not None
        assert crawler._db is runtime.r18_db
        # 旧引擎已释放 (无残留)
        assert runtime._old_r18_db is None
