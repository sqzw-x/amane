"""演员浏览 API 测试. 字段筛选 SQL 见 tests/db/test_actor_browse.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.db.models import FacetKind

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestActorsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_detail_scrape_and_patch(self, client: AsyncClient, repo: Repository, stop_worker: None) -> None:
        await repo.upsert_metadata(number="ACT-BR-1", actors=["EmptyOne", "FilledOne"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        filled_id = next(a.id for a in actors if a.name == "FilledOne")
        assert filled_id is not None

        filled = await repo.get_actor(filled_id)
        assert filled is not None
        filled.birthday = "1991-01-01"
        filled.height = 160
        filled.image_urls = ["https://img.example/a.jpg"]
        filled.overview = "bio-not-for-list"
        await repo.save_actor(filled, aliases=["HiddenFromList"])

        resp = await client.get("actors")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        names = {i["name"] for i in body["items"]}
        assert "EmptyOne" in names and "FilledOne" in names
        listed = next(i for i in body["items"] if i["id"] == filled_id)
        assert listed["overview"] is None
        assert listed["aliases"] == []
        assert listed["image_urls"] == ["https://img.example/a.jpg"]

        detail = await client.get(f"actors/{filled_id}")
        assert detail.status_code == 200
        assert detail.json()["birthday"] == "1991-01-01"
        assert detail.json()["gender"] == "unknown"
        assert detail.json()["count"] >= 1
        assert detail.json()["overview"] == "bio-not-for-list"
        assert detail.json()["aliases"] == ["HiddenFromList"]
        assert "raw" in detail.json()

        patched = await client.patch(
            f"actors/{filled_id}",
            json={
                "overview": "edited bio",
                "gender": "female",
                "image_urls": ["https://img.example/b.jpg", "https://img.example/a.jpg"],
                "birthday": None,
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["overview"] == "edited bio"
        assert body["gender"] == "female"
        assert body["image_urls"][0] == "https://img.example/b.jpg"
        assert body["birthday"] is None

        empty_patch = await client.patch(f"actors/{filled_id}", json={})
        assert empty_patch.status_code == 422

        scrape = await client.post(f"actors/{filled_id}/scrape")
        assert scrape.status_code == 202
        assert scrape.json()["type"] == "actor_scrape"
        assert scrape.json()["payload"]["actor_id"] == filled_id
        assert set(scrape.json()["payload"]["use_cache"]) == {"metadata", "trans"}

        force = await client.post(f"actors/{filled_id}/scrape", json={"use_cache": []})
        assert force.status_code == 202
        assert force.json()["id"] == scrape.json()["id"]

        missing = await client.get("actors/99999")
        assert missing.status_code == 404

        missing_patch = await client.patch("actors/99999", json={"overview": "x"})
        assert missing_patch.status_code == 404

        bad_bday = await client.patch(f"actors/{filled_id}", json={"birthday": "not-a-date"})
        assert bad_bday.status_code == 422

        norm_bday = await client.patch(f"actors/{filled_id}", json={"birthday": "1991年1月1日"})
        assert norm_bday.status_code == 200
        assert norm_bday.json()["birthday"] == "1991-01-01"

        inverted = await client.get("actors", params={"height_min": 200, "height_max": 150})
        assert inverted.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_user_tags(self, client: AsyncClient, repo: Repository) -> None:
        """演员标签的挂载 / 卸载 / 筛选与详情响应; 未知标签是请求级 404."""
        await repo.upsert_metadata(number="ACT-UT-1", actors=["TagMe", "Other"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        tag_me = next(a.id for a in actors if a.name == "TagMe")
        other = next(a.id for a in actors if a.name == "Other")
        assert tag_me is not None and other is not None
        created = await client.post("facets/user_tag", json={"names": ["收藏", "稍后看"]})
        assert created.status_code == 200
        tag_id, later_id = [tag["id"] for tag in created.json()["items"]]

        attached = await client.post(
            "actors/batch/user-tags",
            json={"ids": [tag_me, other, 9999], "user_tag_ids": [tag_id, later_id], "action": "attach"},
        )
        assert attached.status_code == 200
        assert attached.json() == {"changed": 2, "unchanged": 0, "missing": 1}

        # 详情携带标签; 列表不携带
        detail = await client.get(f"actors/{tag_me}")
        assert detail.status_code == 200
        assert [t["name"] for t in detail.json()["user_tags"]] == ["收藏", "稍后看"]
        listed = await client.get("actors", params={"user_tag_ids": [tag_id]})
        assert listed.status_code == 200
        assert {i["name"] for i in listed.json()["items"]} == {"TagMe", "Other"}
        # 列表行不带标签 (与简介 / 别名同为一律留空, 标签只在详情填充)
        assert listed.json()["items"][0]["user_tags"] == []

        # PATCH 之后响应仍带标签
        patched = await client.patch(f"actors/{tag_me}", json={"overview": "bio"})
        assert [t["name"] for t in patched.json()["user_tags"]] == ["收藏", "稍后看"]

        # 已处于目标态计入 unchanged; 未挂载的卸载同样是 unchanged
        again = await client.post(
            "actors/batch/user-tags", json={"ids": [tag_me], "user_tag_ids": [tag_id], "action": "attach"}
        )
        assert again.json() == {"changed": 0, "unchanged": 1, "missing": 0}
        removed = await client.post(
            "actors/batch/user-tags",
            json={"ids": [tag_me, other], "user_tag_ids": [tag_id, later_id], "action": "detach"},
        )
        assert removed.json() == {"changed": 2, "unchanged": 0, "missing": 0}
        assert (await client.get(f"actors/{tag_me}")).json()["user_tags"] == []

        unknown = await client.post(
            "actors/batch/user-tags", json={"ids": [tag_me], "user_tag_ids": [9999], "action": "attach"}
        )
        assert unknown.status_code == 404
        # 空入参两条都显式带 action, 否则 422 可能来自缺失字段而非长度约束
        assert (
            await client.post("actors/batch/user-tags", json={"ids": [], "user_tag_ids": [tag_id], "action": "attach"})
        ).status_code == 422
        assert (
            await client.post("actors/batch/user-tags", json={"ids": [tag_me], "user_tag_ids": [], "action": "attach"})
        ).status_code == 422
