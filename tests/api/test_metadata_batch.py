"""/metadata/batch/* HTTP 接线. 删除级联 / 挂载语义见 tests/db."""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from amane.db.models import TaskType

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


class TestMetadataBatchHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_delete(self, client: AsyncClient, repo: Repository):
        m1 = await repo.upsert_metadata(number="BD-001")
        m2 = await repo.upsert_metadata(number="BD-002")
        assert m1.id is not None and m2.id is not None
        resp = await client.post("metadata/batch/delete", json={"ids": [m1.id, m2.id, 9999]})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2, "missing": 1}
        assert (await client.post("metadata/batch/delete", json={"ids": [8888]})).json() == {
            "deleted": 0,
            "missing": 1,
        }
        assert (await client.post("metadata/batch/delete", json={"ids": []})).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_scrape(self, client: AsyncClient, repo: Repository, stop_worker: None):
        m1 = await repo.upsert_metadata(number="BS-001")
        m2 = await repo.upsert_metadata(number="BS-002")
        assert m1.id is not None and m2.id is not None
        resp = await client.post("metadata/batch/scrape", json={"ids": [m1.id, m2.id, 9999]})
        assert resp.status_code == 202
        data = resp.json()
        assert data["submitted"] == 2
        assert data["missing"] == 1
        assert len(data["task_ids"]) == 2
        numbers = set()
        for task_id in data["task_ids"]:
            task = await repo.get_task(task_id)
            assert task is not None
            assert task.type == TaskType.SCRAPE
            numbers.add(task.payload["number"])
        assert numbers == {"BS-001", "BS-002"}
        task = await repo.get_task(data["task_ids"][0])
        assert task is not None
        assert task.payload["content_type"] == "censored"
        assert set(task.payload["use_cache"]) == {"metadata", "trans"}

        custom_meta = await repo.upsert_metadata(number="BS-004")
        assert custom_meta.id is not None
        custom = await client.post(
            "metadata/batch/scrape", json={"ids": [custom_meta.id], "content_type": "uncensored", "use_cache": []}
        )
        custom_task = await repo.get_task(custom.json()["task_ids"][0])
        assert custom_task is not None
        assert custom_task.payload["content_type"] == "uncensored"
        assert custom_task.payload["use_cache"] == []

        missing = await client.post("metadata/batch/scrape", json={"ids": [9999]})
        assert missing.status_code == 202
        assert missing.json() == {"submitted": 0, "missing": 1, "task_ids": []}
        assert (await client.post("metadata/batch/scrape", json={"ids": []})).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_user_tags(self, client: AsyncClient, repo: Repository):
        tags, _created = await repo.ensure_user_tags(["watched"])
        tag = tags[0]
        assert tag.id is not None
        m1 = await repo.upsert_metadata(number="BT-001")
        m2 = await repo.upsert_metadata(number="BT-002")
        assert m1.id is not None and m2.id is not None
        resp = await client.post(
            "metadata/batch/user-tags",
            json={"ids": [m1.id, m2.id, 9999], "user_tag_ids": [tag.id], "action": "attach"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"changed": 2, "unchanged": 0, "missing": 1}
        detail = await client.get(f"metadata/{m1.id}")
        assert any(t["name"] == "watched" for t in detail.json()["user_tags"])

        detach = await client.post(
            "metadata/batch/user-tags",
            json={"ids": [m1.id, m2.id], "user_tag_ids": [tag.id], "action": "detach"},
        )
        assert detach.json() == {"changed": 2, "unchanged": 0, "missing": 0}
        # 未挂载的条目在 detach 时计入 unchanged, 不是错误
        assert (
            await client.post(
                "metadata/batch/user-tags", json={"ids": [m1.id], "user_tag_ids": [tag.id], "action": "detach"}
            )
        ).json() == {"changed": 0, "unchanged": 1, "missing": 0}
        # 未知标签是请求级错误, 整请求不生效
        unknown = await client.post(
            "metadata/batch/user-tags", json={"ids": [m1.id], "user_tag_ids": [9999], "action": "attach"}
        )
        assert unknown.status_code == 404
        assert (
            await client.post("metadata/batch/user-tags", json={"ids": [m1.id], "user_tag_ids": [], "action": "attach"})
        ).status_code == 422
        assert (
            await client.post(
                "metadata/batch/user-tags", json={"ids": [m1.id], "user_tag_ids": [tag.id], "action": "bogus"}
            )
        ).status_code == 422
        assert (
            await client.post(
                "metadata/batch/user-tags", json={"ids": [], "user_tag_ids": [tag.id], "action": "attach"}
            )
        ).status_code == 422
