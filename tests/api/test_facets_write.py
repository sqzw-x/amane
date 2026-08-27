"""/facets HTTP 接线: 状态码与 JSON. 重命名/合并/规则语义见 tests/db/test_facets.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


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
    tag = await repo.create_user_tag("old")
    assert tag.id is not None
    renamed = await client.patch(f"facets/user_tag/{tag.id}", json={"name": "new"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new"

    await repo.create_user_tag("taken")
    mine = await repo.create_user_tag("mine")
    assert mine.id is not None
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": "taken"})).status_code == 409
    assert (await client.patch("facets/user_tag/9999", json={"name": "x"})).status_code == 404
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": "   "})).status_code == 400
    assert (await client.patch(f"facets/user_tag/{mine.id}", json={"name": ""})).status_code == 422
    assert (await client.patch("facets/not_a_kind/1", json={"name": "x"})).status_code == 422

    target = await repo.create_user_tag("target")
    source = await repo.create_user_tag("source")
    assert target.id is not None and source.id is not None
    meta = await repo.upsert_metadata(number="HTTP-UT-1")
    assert meta.id is not None
    await repo.attach_user_tag(meta.id, source.id)
    merged = await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [source.id]})
    assert merged.status_code == 200
    assert merged.json()["name"] == "target"
    assert (await client.get(f"facets/user_tag/{source.id}")).status_code == 404
    assert (
        await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [9999]})
    ).status_code == 400

    doomed = await repo.create_user_tag("doomed")
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
