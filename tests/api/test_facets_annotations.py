"""分类索引 / 用户注解 API 测试."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestFacetsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_and_filter(self, client: AsyncClient, repo: Repository) -> None:
        await repo.upsert_metadata(number="F-001", actors=["Alice"], studio="StudioX", tags=["hot"])
        resp = await client.get("facets/actor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        actor_id = next(i["id"] for i in data["items"] if i["name"] == "Alice")

        resp = await client.get(f"metadata?actor_id={actor_id}")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        await repo.upsert_metadata(number="F-002", actors=["Alice", "Bob"])
        resp = await client.get("facets/actor")
        bob_id = next(i["id"] for i in resp.json()["items"] if i["name"] == "Bob")
        resp = await client.get(f"metadata?actor_id={actor_id}&actor_id={bob_id}")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["number"] == "F-002"

        resp = await client.get(f"facets/actor/{actor_id}")
        assert resp.status_code == 200
        catalog = resp.json()
        assert set(catalog) == {"id", "name", "count"}
        assert catalog["id"] == actor_id
        assert catalog["name"] == "Alice"
        assert catalog["count"] >= 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_kind(self, client: AsyncClient) -> None:
        resp = await client.get("facets/not_a_kind")
        assert resp.status_code == 422


class TestUserTagsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_crud_and_apply(self, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="UT-API-1")
        assert meta.id is not None

        created = await client.post("facets/user_tag", json={"names": ["watched", "later"]})
        assert created.status_code == 200
        assert created.json()["created"] == 2
        tag_id, other_id = [tag["id"] for tag in created.json()["items"]]

        # 名称已存在时复用原行, 不再 409
        again = await client.post("facets/user_tag", json={"names": ["watched"]})
        assert again.status_code == 200
        assert again.json()["created"] == 0
        assert again.json()["items"][0]["id"] == tag_id

        # 去重与去空白后为空 → 422
        deduped = await client.post("facets/user_tag", json={"names": ["dup", " dup ", "dup"]})
        assert deduped.json()["created"] == 1
        assert (await client.post("facets/user_tag", json={"names": []})).status_code == 422
        assert (await client.post("facets/user_tag", json={"names": ["   "]})).status_code == 422

        # 单条写入是 1×1 的批量: 两个维度都是集合
        resp = await client.post(
            "metadata/batch/user-tags",
            json={"ids": [meta.id], "user_tag_ids": [tag_id, other_id], "action": "attach"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"changed": 1, "unchanged": 0, "missing": 0}

        data = (await client.get(f"metadata/{meta.id}")).json()
        assert {t["name"] for t in data["user_tags"]} == {"watched", "later"}

        # 已处于目标态计入 unchanged, 不报错
        again = await client.post(
            "metadata/batch/user-tags",
            json={"ids": [meta.id], "user_tag_ids": [tag_id], "action": "attach"},
        )
        assert again.json() == {"changed": 0, "unchanged": 1, "missing": 0}

        removed = await client.post(
            "metadata/batch/user-tags",
            json={"ids": [meta.id], "user_tag_ids": [tag_id, other_id], "action": "detach"},
        )
        assert removed.json() == {"changed": 1, "unchanged": 0, "missing": 0}
        assert (await client.get(f"metadata/{meta.id}")).json()["user_tags"] == []

        denied = await client.post("facets/studio", json={"name": "Nope"})
        assert denied.status_code == 405


class TestCommentsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_comment_lifecycle(self, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="CM-API-1")
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/comments", json={"body": "  nice  "})
        assert resp.status_code == 201
        created = resp.json()
        assert created["body"] == "nice"
        assert created["created_at"] == created["updated_at"]
        comment_id = created["id"]

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.status_code == 200
        assert [c["id"] for c in detail.json()["comments"]] == [comment_id]

        resp = await client.patch(f"comments/{comment_id}", json={"body": "updated"})
        assert resp.status_code == 200
        edited = resp.json()
        assert edited["body"] == "updated"
        assert datetime.fromisoformat(edited["updated_at"]) > datetime.fromisoformat(created["updated_at"])

        # 与库中一致的正文不构成编辑, 不刷新编辑时间.
        resp = await client.patch(f"comments/{comment_id}", json={"body": "updated"})
        assert resp.status_code == 200
        assert resp.json()["updated_at"] == edited["updated_at"]

        resp = await client.delete(f"comments/{comment_id}")
        assert resp.status_code == 204

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["comments"] == []

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("number", "body", "status"),
        [
            ("CM-VALID-EMPTY", "", 422),
            ("CM-VALID-BLANK", "   ", 422),
            ("CM-VALID-WS", "\n\t ", 422),
            ("CM-VALID-MAX", "x" * 10000, 201),
            ("CM-VALID-OVER", "x" * 10001, 422),
        ],
    )
    async def test_create_body_validation(
        self, client: AsyncClient, repo: Repository, number: str, body: str, status: int
    ) -> None:
        meta = await repo.upsert_metadata(number=number)
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/comments", json={"body": body})
        assert resp.status_code == status
        if status == 201:
            assert resp.json()["body"] == body

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ("", 422),
            ("   ", 422),
            ("x" * 10001, 422),
        ],
    )
    async def test_update_body_validation(self, client: AsyncClient, repo: Repository, body: str, status: int) -> None:
        meta = await repo.upsert_metadata(number="CM-API-2")
        assert meta.id is not None
        created = (await client.post(f"metadata/{meta.id}/comments", json={"body": "keep me"})).json()

        resp = await client.patch(f"comments/{created['id']}", json={"body": body})
        assert resp.status_code == status

        detail = await client.get(f"metadata/{meta.id}")
        assert [c["body"] for c in detail.json()["comments"]] == ["keep me"]

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("method", "url", "payload"),
        [
            ("post", "metadata/999999/comments", {"body": "orphan"}),
            ("patch", "comments/999999", {"body": "orphan"}),
            ("delete", "comments/999999", None),
        ],
    )
    async def test_missing_target(
        self, client: AsyncClient, method: str, url: str, payload: dict[str, str] | None
    ) -> None:
        resp = await client.request(method, url, json=payload)
        assert resp.status_code == 404
