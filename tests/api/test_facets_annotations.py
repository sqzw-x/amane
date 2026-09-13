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
    async def test_crud_and_attach(self, client: AsyncClient, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="UT-API-1")
        assert meta.id is not None

        resp = await client.post("facets/user_tag", json={"name": "watched"})
        assert resp.status_code == 201
        tag_id = resp.json()["id"]

        resp = await client.put(f"metadata/{meta.id}/user-tags/{tag_id}")
        assert resp.status_code == 204

        resp = await client.get(f"metadata/{meta.id}")
        assert resp.status_code == 200
        assert any(t["name"] == "watched" for t in resp.json()["user_tags"])

        resp = await client.delete(f"metadata/{meta.id}/user-tags/{tag_id}")
        assert resp.status_code == 204

        resp = await client.post("facets/user_tag", json={"name": "watched"})
        assert resp.status_code == 409

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
