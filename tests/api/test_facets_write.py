"""/facets HTTP 接线: 状态码与 JSON. 重命名/合并/规则语义见 tests/db/test_facets.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.db.models import UserTag

if TYPE_CHECKING:
    from httpx2 import AsyncClient

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
