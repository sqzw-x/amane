"""/schedules 端点测试"""

from typing import TYPE_CHECKING

import pytest

from amane.db.models import RoutineType

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestSchedules:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_crud_and_trigger(self, client: AsyncClient, repo: Repository):
        empty = await client.get("schedules")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0

        created = await client.post(
            "schedules",
            json={
                "cron": "0 */6 * * *",
                "submission": {"type": "cleanup", "remove_missing_files": False},
            },
        )
        assert created.status_code == 201
        data = created.json()
        assert data["cron"] == "0 */6 * * *"
        assert data["task_type"] == "cleanup"
        assert data["enabled"] is True
        assert data["payload"]["remove_missing_files"] is False
        fetched = await client.get(f"schedules/{data['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == data["id"]
        assert (await client.get("schedules/9999")).status_code == 404

        named = await client.post(
            "schedules",
            json={
                "cron": "0 8 * * *",
                "name": "daily",
                "enabled": False,
                "submission": {"type": "cleanup"},
            },
        )
        assert named.json()["name"] == "daily"
        assert named.json()["enabled"] is False

        upscale = await client.post(
            "schedules",
            json={"cron": "0 3 * * *", "submission": {"type": "upscale", "limit": 50}},
        )
        assert upscale.status_code == 201
        assert upscale.json()["task_type"] == "upscale"
        assert upscale.json()["payload"]["limit"] == 50

        listed = await client.get("schedules")
        assert listed.json()["total"] == 3

        sched = await repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={}, name="old")
        patched = await client.patch(f"schedules/{sched.id}", json={"name": "updated", "enabled": False})
        assert patched.json()["name"] == "updated"
        assert patched.json()["enabled"] is False
        meta = await client.patch(f"schedules/{sched.id}", json={"name": "renamed", "cron": "0 6 * * *"})
        assert meta.json()["cron"] == "0 6 * * *"
        assert meta.json()["task_type"] == "cleanup"
        assert (await client.patch("schedules/9999", json={"name": "x"})).status_code == 404
        assert (await client.patch(f"schedules/{sched.id}", json={"cron": "bad"})).status_code == 422

        triggered = await client.post(f"schedules/{sched.id}/trigger")
        assert triggered.status_code == 200
        assert triggered.json()["next_run"] is not None
        assert (await client.post("schedules/9999/trigger")).status_code == 404

        deleted = await client.delete(f"schedules/{sched.id}")
        assert deleted.status_code == 204
        assert (await client.delete("schedules/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_invalid_payloads(self, client: AsyncClient):
        assert (
            await client.post("schedules", json={"cron": "invalid", "submission": {"type": "cleanup"}})
        ).status_code == 422
        assert (
            await client.post("schedules", json={"cron": "0 * * * *", "submission": {"type": "invalid_type"}})
        ).status_code == 422
