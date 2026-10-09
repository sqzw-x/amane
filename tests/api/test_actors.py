"""演员浏览 API 测试. 字段筛选 SQL 见 tests/db/test_actor_browse.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PIL import Image

from amane.db.models import Actor, FacetKind
from amane.enums import ActorGender

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
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
    async def test_actor_locks(self, client: AsyncClient, repo: Repository) -> None:
        """锁端点 / PATCH 自动上锁 / 锁列不可经 PATCH 写 / clear-person 整体重置."""
        await repo.upsert_metadata(number="ACT-LOCK-1", actors=["Locky"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        actor_id = next(a.id for a in actors if a.name == "Locky")
        assert actor_id is not None

        # 手动 PATCH 自动把写入字段并入锁; 请求携带 locked_fields 被忽略.
        patched = await client.patch(f"actors/{actor_id}", json={"overview": "bio", "locked_fields": []})
        assert patched.status_code == 200
        assert patched.json()["overview"] == "bio"
        assert patched.json()["locked_fields"] == ["overview"]

        # PUT /locks 整体替换: 重复值去重且按枚举声明序; 别名同属可锁集合.
        locked = await client.put(
            f"actors/{actor_id}/locks", json={"fields": ["aliases", "image_urls", "gender", "gender"]}
        )
        assert locked.status_code == 200
        assert locked.json()["locked_fields"] == ["gender", "image_urls", "aliases"]

        # 自动刮削写入跳过锁定字段 (gender 被锁保留, 未锁定 birthday 正常写入).
        actor = await repo.get_actor(actor_id)
        assert actor is not None
        actor.gender = ActorGender.FEMALE
        actor.birthday = "2000-01-01"
        actor.field_sources = {"gender": "minnano"}
        saved = await repo.save_actor(actor)
        assert saved is not None
        assert saved.gender == "unknown"
        assert saved.birthday == "2000-01-01"

        bad = await client.put(f"actors/{actor_id}/locks", json={"fields": ["bogus"]})
        assert bad.status_code == 422
        assert (await client.put("actors/99999/locks", json={"fields": []})).status_code == 404

        # 清空信息: 字段 / 别名 / 刮削缓存清空并解除全部锁, name 与 gender 保留.
        aliased = await client.patch(
            f"actors/{actor_id}", json={"aliases": ["Nick"], "birthday": "1991-01-01", "gender": "female"}
        )
        assert aliased.status_code == 200
        seeded = await repo.get_actor(actor_id)
        assert seeded is not None
        seeded.raw = {"minnano": {"birthday": "1991-01-01"}}
        await repo.save_actor(seeded)

        cleared = await client.post(f"actors/{actor_id}/clear-person")
        assert cleared.status_code == 200
        body = cleared.json()
        assert body["name"] == "Locky"
        assert body["gender"] == "female"
        assert body["birthday"] is None
        assert body["aliases"] == []
        assert body["locked_fields"] == []
        assert body["raw"] == {}
        assert (await client.post("actors/99999/clear-person")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_actor_response_drops_unknown_locked_values(self, client: AsyncClient, repo: Repository) -> None:
        """库内非法存量锁值不使读路径 500, 并去重."""
        await repo.upsert_metadata(number="ACT-LOCK-2", actors=["Dirty"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        actor_id = next(a.id for a in actors if a.name == "Dirty")
        assert actor_id is not None
        async with repo._session() as session:
            row = await session.get(Actor, actor_id)
            assert row is not None
            row.locked_fields = ["birthday", "birthday", "bogus"]
            session.add(row)
            await session.commit()

        detail = await client.get(f"actors/{actor_id}")
        assert detail.status_code == 200
        assert detail.json()["locked_fields"] == ["birthday"]

        listed = await client.get("actors", params={"search": "Dirty"})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["locked_fields"] == []

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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_crop_avatar(self, client: AsyncClient, repo: Repository, app: FastAPI) -> None:
        """外部源裁切 → 内部 URL 前插并保留原图; 再以内部源裁切; 边界与非法输入."""
        await repo.upsert_metadata(number="ACT-CROP-1", actors=["CropMe"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        actor_id = next(a.id for a in actors if a.name == "CropMe")
        actor = await repo.get_actor(actor_id)
        assert actor is not None
        actor.image_urls = ["https://img.example/orig.jpg"]
        await repo.save_actor(actor)

        async def fake_download(url: str, dest: Path, **kwargs: object) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "blue").save(dest)
            return True

        async def fake_resolve(url: str, **kwargs: object) -> str:
            return url

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        app.state.runtime.web_client.resolve_final_url = fake_resolve  # type: ignore[method-assign]

        first = await client.post(
            f"actors/{actor_id}/crop-avatar", json={"left": 0, "top": 0, "right": 400, "bottom": 538}
        )
        assert first.status_code == 200
        urls = first.json()["image_urls"]
        assert len(urls) == 2
        assert urls[0].startswith("/api/resources/")
        assert urls[1] == "https://img.example/orig.jpg"
        # 裁切写入 image_urls 并自动锁定.
        assert first.json()["locked_fields"] == ["image_urls"]

        # 主图已变: 再次裁切以上一层裁切结果为源, 追加新派生项并保留全部历史
        second = await client.post(
            f"actors/{actor_id}/crop-avatar", json={"left": 0, "top": 0, "right": 200, "bottom": 269}
        )
        assert second.status_code == 200
        second_urls = second.json()["image_urls"]
        assert second_urls[0].startswith("/api/resources/") and second_urls[0] != urls[0]
        assert second_urls[1:] == urls

        # 非法框 (left >= right) 在模型层拒绝
        inverted = await client.post(
            f"actors/{actor_id}/crop-avatar", json={"left": 100, "top": 0, "right": 50, "bottom": 100}
        )
        assert inverted.status_code == 422

        # 无图演员与不存在的演员
        await repo.upsert_metadata(number="ACT-CROP-2", actors=["NoImage"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        no_image_id = next(a.id for a in actors if a.name == "NoImage")
        no_image = await client.post(
            f"actors/{no_image_id}/crop-avatar", json={"left": 0, "top": 0, "right": 10, "bottom": 10}
        )
        assert no_image.status_code == 400
        assert "头像" in no_image.json()["detail"]
        missing = await client.post("actors/99999/crop-avatar", json={"left": 0, "top": 0, "right": 10, "bottom": 10})
        assert missing.status_code == 404
