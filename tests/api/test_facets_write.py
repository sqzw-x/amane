"""/facets HTTP 接线: 状态码与 JSON. 重命名/合并/规则语义见 tests/db/test_facets.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.db.models import UserTag

if TYPE_CHECKING:
    from httpx2 import AsyncClient, Response

    from amane.db.repository import Repository


async def _tag(repo: Repository, name: str) -> UserTag:
    """测试便捷入口: 按名称取回或新建单个用户标签."""
    tags, _created = await repo.ensure_user_tags([name])
    return tags[0]


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_http_actor_rename_merge_delete(client: AsyncClient, repo: Repository) -> None:
    meta = await repo.upsert_metadata(number="HTTP-RN-1", actors=["Alice", "Carol"])
    assert meta.id is not None
    listed = await client.get("facets/actor?search=Alice")
    assert listed.status_code == 200
    facet_id = next(i["id"] for i in listed.json()["items"] if i["name"] == "Alice")

    renamed = await client.patch(f"facets/actor/{facet_id}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    detail = await client.get(f"metadata/{meta.id}")
    assert detail.json()["metadata"]["actors"] == ["Renamed", "Carol"]

    noop = await client.patch(f"facets/actor/{facet_id}", json={"name": "Renamed"})
    assert noop.status_code == 200
    assert (await client.patch("facets/actor/9999", json={"name": "X"})).status_code == 404

    await repo.upsert_metadata(number="HTTP-RN-2a", actors=["DupA"])
    await repo.upsert_metadata(number="HTTP-RN-2b", actors=["DupB"])
    dup_id = next(
        i["id"] for i in (await client.get("facets/actor?search=DupA")).json()["items"] if i["name"] == "DupA"
    )
    conflict = await client.patch(f"facets/actor/{dup_id}", json={"name": "DupB"})
    assert conflict.status_code == 409
    assert "合并" in conflict.json()["detail"]

    await repo.upsert_metadata(number="HTTP-MG-a", actors=["A"])
    await repo.upsert_metadata(number="HTTP-MG-b", actors=["B"])
    await repo.upsert_metadata(number="HTTP-MG-c", actors=["A", "B", "Other"])
    target_id = next(i["id"] for i in (await client.get("facets/actor?search=A")).json()["items"] if i["name"] == "A")
    source_id = next(i["id"] for i in (await client.get("facets/actor?search=B")).json()["items"] if i["name"] == "B")
    assert (
        await client.post("facets/actor/merge", json={"target_id": target_id, "source_ids": [9999]})
    ).status_code == 400
    assert (
        await client.post("facets/actor/merge", json={"target_id": 9999, "source_ids": [source_id]})
    ).status_code == 404
    assert (
        await client.post("facets/actor/merge", json={"target_id": target_id, "source_ids": [target_id]})
    ).status_code == 400
    merged = await client.post("facets/actor/merge", json={"target_id": target_id, "source_ids": [source_id]})
    assert merged.status_code == 200
    assert merged.json()["name"] == "A"
    assert (await client.get(f"facets/actor/{source_id}")).status_code == 404

    doomed = next(
        i["id"] for i in (await client.get("facets/actor?search=Carol")).json()["items"] if i["name"] == "Carol"
    )
    assert (await client.delete(f"facets/actor/{doomed}")).status_code == 204
    rules = (await client.get("facets/actor/rules")).json()["items"]
    assert any(r["source_name"] == "Carol" and r["action"] == "block" for r in rules)
    rule = next(r for r in rules if r["source_name"] == "Carol")
    assert (await client.delete(f"facets/actor/rules/{rule['id']}")).status_code == 204
    assert (await client.delete("facets/actor/9999")).status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_http_user_tag_and_validation(client: AsyncClient, repo: Repository) -> None:
    tag = await _tag(repo, "old")
    assert tag.id is not None
    renamed = await client.patch(f"facets/user_tag/{tag.id}", json={"name": "new"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new"

    await _tag(repo, "taken")
    mine = await _tag(repo, "mine")
    assert mine.id is not None
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": "taken"})).status_code == 409
    assert (await client.patch("facets/user_tag/9999", json={"name": "x"})).status_code == 404
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": "   "})).status_code == 400
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": ""})).status_code == 422
    assert (await client.patch("facets/not_a_kind/1", json={"name": "x"})).status_code == 422

    target = await _tag(repo, "target")
    source = await _tag(repo, "source")
    assert target.id is not None and source.id is not None
    meta = await repo.upsert_metadata(number="HTTP-UT-1")
    assert meta.id is not None
    await repo.apply_metadata_user_tags([meta.id], [source.id], action="attach")
    merged = await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [source.id]})
    assert merged.status_code == 200
    assert merged.json()["name"] == "target"
    assert (await client.get(f"facets/user_tag/{source.id}")).status_code == 404
    assert (
        await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [9999]})
    ).status_code == 400

    doomed = await _tag(repo, "doomed")
    assert doomed.id is not None
    assert (await client.delete(f"facets/user_tag/{doomed.id}")).status_code == 204
    assert (await client.get("facets/user_tag/rules")).status_code == 400


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_http_scalar_rename(client: AsyncClient, repo: Repository) -> None:
    m1 = await repo.upsert_metadata(number="HTTP-SC-1", studio="Old")
    m2 = await repo.upsert_metadata(number="HTTP-SC-2", studio="Old")
    assert m1.id is not None and m2.id is not None
    facet_id = next(
        i["id"] for i in (await client.get("facets/studio?search=Old")).json()["items"] if i["name"] == "Old"
    )
    resp = await client.patch(f"facets/studio/{facet_id}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
    assert resp.json()["count"] == 2
    for meta in (m1, m2):
        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"]["studio"] == "New"


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_merge_via_http_carries_user_tags(client: AsyncClient, repo: Repository) -> None:
    """演员合并经 HTTP: 源演员的标签挂载并入 target, 不留悬挂行."""
    await repo.upsert_metadata(number="HTTP-AM-1", actors=["Canonical"])
    await repo.upsert_metadata(number="HTTP-AM-2", actors=["Other"])
    listed = (await client.get("facets/actor")).json()["items"]
    target = next(i["id"] for i in listed if i["name"] == "Canonical")
    source = next(i["id"] for i in listed if i["name"] == "Other")
    created = await client.post("facets/user_tag", json={"names": ["收藏", "稍后看"]})
    tag_id, later_id = [tag["id"] for tag in created.json()["items"]]
    await repo.apply_actor_user_tags([target, source], [tag_id, later_id], action="attach")
    assert (await client.delete(f"facets/user_tag/{later_id}")).status_code == 204

    merged = await client.post("facets/actor/merge", json={"target_id": target, "source_ids": [source]})
    assert merged.status_code == 200
    tags = (await client.get(f"actors/{target}")).json()["user_tags"]
    assert [t["name"] for t in tags] == ["收藏"]
    assert (await client.get(f"actors/{source}")).status_code == 404


async def _facet_id(client: AsyncClient, kind: str, name: str) -> int:
    """测试便捷入口: 按名称取回分类 id."""
    resp = await client.get(f"facets/{kind}?search={name}")
    assert resp.status_code == 200
    return next(i["id"] for i in resp.json()["items"] if i["name"] == name)


async def _batch(client: AsyncClient, kind: str, facet_ids: list[int], action: str | None) -> Response:
    """测试便捷入口: 提交一次批量动作; 非枚举取值用于非法动作的负例."""
    return await client.post(f"facets/{kind}/batch", json={"facet_ids": facet_ids, "action": action})


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_favorite_http(client: AsyncClient, repo: Repository) -> None:
    """收藏端点与筛选的接线: 状态码、响应字段与不支持收藏的分类."""
    await repo.upsert_metadata(number="HTTP-FV-1", studio="FavStudio")
    await repo.upsert_metadata(number="HTTP-FV-2", studio="PlainStudio", tags=["FavTag"], actors=["Alice"])
    studio_id = await _facet_id(client, "studio", "FavStudio")

    listed = (await client.get("facets/studio")).json()["items"]
    assert listed and all(i["is_favorite"] is False for i in listed)

    # 单条写入经动作端点的单元素列表表达
    marked = await _batch(client, "studio", [studio_id], "favorite")
    assert marked.status_code == 200
    assert marked.json() == {"changed": 1, "unchanged": 0, "missing": 0}
    assert (await client.get(f"facets/studio/{studio_id}")).json()["is_favorite"] is True

    only_favorite = (await client.get("facets/studio?favorite=true")).json()
    assert [i["name"] for i in only_favorite["items"]] == ["FavStudio"]
    assert only_favorite["total"] == 1
    without = (await client.get("facets/studio?favorite=false")).json()
    assert [i["name"] for i in without["items"]] == ["PlainStudio"]

    # 幂等: 重复提交同一动作不产生写入; 取消收藏回到假值
    assert (await _batch(client, "studio", [studio_id], "favorite")).json() == {
        "changed": 0,
        "unchanged": 1,
        "missing": 0,
    }
    cleared = await _batch(client, "studio", [studio_id], "unfavorite")
    assert cleared.status_code == 200
    assert cleared.json() == {"changed": 1, "unchanged": 0, "missing": 0}
    assert (await client.get("facets/studio?favorite=true")).json()["items"] == []

    # 写入已收敛到动作端点: 旧的单条路径不再存在
    assert (await client.put(f"facets/studio/{studio_id}/favorite", json={"is_favorite": True})).status_code == 404

    # 不存在的 id 计入 missing 而不报 404; 重复 id 去重后计数, 三者之和等于去重后的条目数
    absent = await _batch(client, "studio", [9999, 9999], "favorite")
    assert absent.status_code == 200
    assert absent.json() == {"changed": 0, "unchanged": 0, "missing": 1}
    assert (await client.get("facets/studio/9999")).status_code == 404

    # 非法请求体: 空列表、缺字段、未知动作与大小写不符都是 422; 未知 kind 同样由 schema 拒绝
    assert (await _batch(client, "studio", [], "favorite")).status_code == 422
    assert (await _batch(client, "studio", [studio_id], "toggle")).status_code == 422
    assert (await _batch(client, "studio", [studio_id], "FAVORITE")).status_code == 422
    assert (await _batch(client, "studio", [studio_id], None)).status_code == 422
    assert (await client.post("facets/studio/batch", json={"facet_ids": [studio_id]})).status_code == 422
    assert (await client.post("facets/studio/batch", json={"action": "favorite"})).status_code == 422
    assert (await _batch(client, "not_a_kind", [studio_id], "favorite")).status_code == 422

    # 不支持收藏的分类: 写与筛选都是 400, 与动作取值无关
    tag_id = await _facet_id(client, "tag", "FavTag")
    actor_id = await _facet_id(client, "actor", "Alice")
    for kind, facet_id in (("actor", actor_id), ("user_tag", 9999)):
        for action in ("favorite", "unfavorite"):
            assert (await _batch(client, kind, [facet_id], action)).status_code == 400
        for value in ("true", "false"):
            assert (await client.get(f"facets/{kind}?favorite={value}")).status_code == 400
    assert (await client.get("facets/actor")).json()["items"][0]["is_favorite"] is False

    # 链接分类 (tag) 与标量分类的列表都带该字段
    assert (await client.get("facets/tag")).json()["items"][0]["is_favorite"] is False
    assert await _facet_id(client, "tag", "FavTag") == tag_id


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_favorite_http_director(client: AsyncClient, repo: Repository) -> None:
    """导演与标签同属链接型分类, 收藏走同一条读写路径."""
    await repo.upsert_metadata(number="HTTP-FVD-1", directors=["FavDirector"])
    await repo.upsert_metadata(number="HTTP-FVD-2", directors=["PlainDirector"])
    director_id = await _facet_id(client, "director", "FavDirector")

    listed = (await client.get("facets/director")).json()["items"]
    assert listed and all(i["is_favorite"] is False for i in listed)

    marked = await _batch(client, "director", [director_id], "favorite")
    assert marked.status_code == 200
    assert marked.json() == {"changed": 1, "unchanged": 0, "missing": 0}
    assert (await client.get(f"facets/director/{director_id}")).json()["is_favorite"] is True

    only_favorite = (await client.get("facets/director?favorite=true")).json()
    assert [i["name"] for i in only_favorite["items"]] == ["FavDirector"]
    assert only_favorite["total"] == 1
    without = (await client.get("facets/director?favorite=false")).json()
    assert [i["name"] for i in without["items"]] == ["PlainDirector"]

    cleared = await _batch(client, "director", [director_id], "unfavorite")
    assert cleared.status_code == 200
    assert cleared.json() == {"changed": 1, "unchanged": 0, "missing": 0}
    assert (await client.get("facets/director?favorite=true")).json()["items"] == []

    assert (await client.get("facets/director/9999")).status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_facet_favorite_batch_http(client: AsyncClient, repo: Repository) -> None:
    """批量动作端点: 两个动作都是整体赋值而非翻转, 混合选择与重复提交的结果一致."""
    await repo.upsert_metadata(number="HTTP-FB-1", studio="FavA")
    await repo.upsert_metadata(number="HTTP-FB-2", studio="FavD")
    await repo.upsert_metadata(number="HTTP-FB-3", studio="FavB")
    await repo.upsert_metadata(number="HTTP-FB-4", studio="FavE")
    await repo.upsert_metadata(number="HTTP-FB-5", tags=["FavTag"], actors=["FavActor"])

    async def names_of(kind: str, term: str, favorite: bool) -> list[str]:
        items = (await client.get(f"facets/{kind}?search={term}&favorite={favorite}")).json()["items"]
        return [i["name"] for i in items]

    plain_a = await _facet_id(client, "studio", "FavA")
    marked_b = await _facet_id(client, "studio", "FavB")
    plain_d = await _facet_id(client, "studio", "FavD")
    marked_e = await _facet_id(client, "studio", "FavE")
    assert (await _batch(client, "studio", [marked_b, marked_e], "favorite")).json() == {
        "changed": 2,
        "unchanged": 0,
        "missing": 0,
    }

    # 混合选择: 已收藏的保持收藏, 未收藏的补齐; 重复 id 先去重, 不存在的 id 计入 missing 而不报 404
    first = await _batch(client, "studio", [plain_a, marked_b, plain_d, marked_e, 9998, 9998], "favorite")
    assert first.status_code == 200
    assert first.json() == {"changed": 2, "unchanged": 2, "missing": 1}
    assert sorted(await names_of("studio", "Fav", True)) == ["FavA", "FavB", "FavD", "FavE"]
    assert await names_of("studio", "Fav", False) == []

    # 幂等: 同一批连续提交两次结果一致, 第二次不产生写入
    second = await _batch(client, "studio", [plain_a, marked_b, plain_d, marked_e, 9998], "favorite")
    assert second.status_code == 200
    assert second.json() == {"changed": 0, "unchanged": 4, "missing": 1}
    assert sorted(await names_of("studio", "Fav", True)) == ["FavA", "FavB", "FavD", "FavE"]

    # unfavorite 与 favorite 对称: 只有实际写库的计 changed
    cleared = await _batch(client, "studio", [plain_a, marked_b, marked_e], "unfavorite")
    assert cleared.status_code == 200
    assert cleared.json() == {"changed": 3, "unchanged": 0, "missing": 0}
    assert await names_of("studio", "FavA", True) == []
    assert await names_of("studio", "FavA", False) == ["FavA"]
    assert await names_of("studio", "FavB", False) == ["FavB"]
    assert await names_of("studio", "FavD", True) == ["FavD"]

    # 同一批再提交一次同样是整体赋值: 全部已处于目标取值
    again = await _batch(client, "studio", [plain_a, marked_b, marked_e], "unfavorite")
    assert again.status_code == 200
    assert again.json() == {"changed": 0, "unchanged": 3, "missing": 0}

    # 混合已收藏与未收藏时以目标取值收尾, 不翻转任何一项
    mixed = await _batch(client, "studio", [plain_a, plain_d], "favorite")
    assert mixed.status_code == 200
    assert mixed.json() == {"changed": 1, "unchanged": 1, "missing": 0}
    assert sorted(await names_of("studio", "Fav", True)) == ["FavA", "FavD"]

    # 链接型分类走同一路径: 它与标量分类共用收藏列
    tag_id = await _facet_id(client, "tag", "FavTag")
    assert (await _batch(client, "tag", [tag_id], "favorite")).json() == {
        "changed": 1,
        "unchanged": 0,
        "missing": 0,
    }
    assert await names_of("tag", "FavTag", True) == ["FavTag"]

    # 不支持收藏的分类两个动作都是 400
    actor_id = await _facet_id(client, "actor", "FavActor")
    for action in ("favorite", "unfavorite"):
        assert (await _batch(client, "actor", [actor_id], action)).status_code == 400
        assert (await _batch(client, "user_tag", [9999], action)).status_code == 400

    # 详情读到的就是批量写入的结果
    assert (await client.get(f"facets/studio/{plain_a}")).json()["is_favorite"] is True
    assert (await client.get(f"facets/studio/{plain_d}")).json()["is_favorite"] is True
