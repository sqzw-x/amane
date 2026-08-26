"""/facets 重命名 / 合并端点测试."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.models import Metadata
    from amane.db.repository import Repository

# (facet kind, Metadata list 字段名) -- 演员/导演/标签均为 list[str] 投影.
LIST_FACET_CASES = [
    ("actor", "actors"),
    ("director", "directors"),
    ("tag", "tags"),
]

# (facet kind, Metadata 标量字段名) -- 厂商/发行商/系列均为标量投影.
SCALAR_FACET_CASES = [
    ("studio", "studio"),
    ("publisher", "publisher"),
    ("series", "series"),
]


async def _seed_list_metadata(repo: Repository, number: str, kind: str, values: list[str]) -> Metadata:
    """按 kind 显式分派创建带 list 投影字段的 metadata (不用动态 kwargs, 保持类型可检查)."""
    if kind == "actor":
        return await repo.upsert_metadata(number=number, actors=values)
    if kind == "director":
        return await repo.upsert_metadata(number=number, directors=values)
    if kind == "tag":
        return await repo.upsert_metadata(number=number, tags=values)
    raise ValueError(f"unknown list facet kind: {kind}")


async def _seed_scalar_metadata(repo: Repository, number: str, kind: str, value: str) -> Metadata:
    """按 kind 显式分派创建带标量投影字段的 metadata (不用动态 kwargs, 保持类型可检查)."""
    if kind == "studio":
        return await repo.upsert_metadata(number=number, studio=value)
    if kind == "publisher":
        return await repo.upsert_metadata(number=number, publisher=value)
    if kind == "series":
        return await repo.upsert_metadata(number=number, series=value)
    raise ValueError(f"unknown scalar facet kind: {kind}")


async def _facet_id_by_name(client: AsyncClient, kind: str, name: str) -> int:
    resp = await client.get(f"facets/{kind}?search={name}")
    items = resp.json()["items"]
    return next(i["id"] for i in items if i["name"] == name)


class TestRenameListFacet:
    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_updates_metadata_list(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        meta = await _seed_list_metadata(repo, f"RN-{kind}-1", kind, ["Alice", "Carol"])
        assert meta.id is not None
        facet_id = await _facet_id_by_name(client, kind, "Alice")

        resp = await client.patch(f"facets/{kind}/{facet_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"][field] == ["Renamed", "Carol"]

    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_conflict_returns_409(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        del field
        await _seed_list_metadata(repo, f"RN-{kind}-2a", kind, ["Alice"])
        await _seed_list_metadata(repo, f"RN-{kind}-2b", kind, ["Bob"])
        facet_id = await _facet_id_by_name(client, kind, "Alice")

        resp = await client.patch(f"facets/{kind}/{facet_id}", json={"name": "Bob"})
        assert resp.status_code == 409
        assert "合并" in resp.json()["detail"]

    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_noop_same_name(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        del field
        await _seed_list_metadata(repo, f"RN-{kind}-3", kind, ["Alice"])
        facet_id = await _facet_id_by_name(client, kind, "Alice")

        resp = await client.patch(f"facets/{kind}/{facet_id}", json={"name": "Alice"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Alice"

    @pytest.mark.parametrize("kind", [c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_not_found(self, client: AsyncClient, kind: str):
        resp = await client.patch(f"facets/{kind}/9999", json={"name": "X"})
        assert resp.status_code == 404


class TestRenameScalarFacet:
    @pytest.mark.parametrize(("kind", "field"), SCALAR_FACET_CASES, ids=[c[0] for c in SCALAR_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_updates_metadata_scalar(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        m1 = await _seed_scalar_metadata(repo, f"RS-{kind}-1a", kind, "Old")
        m2 = await _seed_scalar_metadata(repo, f"RS-{kind}-1b", kind, "Old")
        facet_id = await _facet_id_by_name(client, kind, "Old")

        resp = await client.patch(f"facets/{kind}/{facet_id}", json={"name": "New"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New"
        assert data["count"] == 2

        for meta in (m1, m2):
            detail = await client.get(f"metadata/{meta.id}")
            assert detail.json()["metadata"][field] == "New"

    @pytest.mark.parametrize(("kind", "field"), SCALAR_FACET_CASES, ids=[c[0] for c in SCALAR_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_conflict_returns_409(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        del field
        await _seed_scalar_metadata(repo, f"RS-{kind}-2a", kind, "A")
        await _seed_scalar_metadata(repo, f"RS-{kind}-2b", kind, "B")
        facet_id = await _facet_id_by_name(client, kind, "A")

        resp = await client.patch(f"facets/{kind}/{facet_id}", json={"name": "B"})
        assert resp.status_code == 409


class TestRenameUserTag:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename(self, client: AsyncClient, repo: Repository):
        tag = await repo.create_user_tag("old")
        assert tag.id is not None

        resp = await client.patch(f"facets/user_tag/{tag.id}", json={"name": "new"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_conflict_returns_409(self, client: AsyncClient, repo: Repository):
        await repo.create_user_tag("taken")
        tag = await repo.create_user_tag("mine")
        assert tag.id is not None

        resp = await client.patch(f"facets/user_tag/{tag.id}", json={"name": "taken"})
        assert resp.status_code == 409

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_not_found(self, client: AsyncClient):
        resp = await client.patch("facets/user_tag/9999", json={"name": "x"})
        assert resp.status_code == 404


class TestRenameValidation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_blank_name_after_strip_rejected(self, client: AsyncClient, repo: Repository):
        tag = await repo.create_user_tag("t1")
        assert tag.id is not None
        resp = await client.patch(f"facets/user_tag/{tag.id}", json={"name": "   "})
        assert resp.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_name_rejected_by_schema(self, client: AsyncClient, repo: Repository):
        tag = await repo.create_user_tag("t2")
        assert tag.id is not None
        resp = await client.patch(f"facets/user_tag/{tag.id}", json={"name": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unknown_kind_rejected(self, client: AsyncClient):
        resp = await client.patch("facets/not_a_kind/1", json={"name": "x"})
        assert resp.status_code == 422


class TestMergeListFacet:
    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_moves_and_dedupes(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        """合并两个来源到 target: 各自关联的 metadata list 字段替换为 target 名, 且去重."""
        meta_a = await _seed_list_metadata(repo, f"MG-{kind}-1a", kind, ["A"])
        meta_b = await _seed_list_metadata(repo, f"MG-{kind}-1b", kind, ["B"])
        # 同时关联 A 与 B 的记录, 合并后应去重为单一 target 名
        meta_ab = await _seed_list_metadata(repo, f"MG-{kind}-1c", kind, ["A", "B", "Other"])

        target_id = await _facet_id_by_name(client, kind, "A")
        source_id = await _facet_id_by_name(client, kind, "B")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": target_id, "source_ids": [source_id]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "A"
        assert data["count"] == 3

        for meta in (meta_a, meta_b, meta_ab):
            detail = await client.get(f"metadata/{meta.id}")
            values = detail.json()["metadata"][field]
            assert values.count("A") == 1
            assert "B" not in values

        detail_ab = await client.get(f"metadata/{meta_ab.id}")
        assert detail_ab.json()["metadata"][field] == ["A", "Other"]

        # source 实体已被删除
        resp = await client.get(f"facets/{kind}/{source_id}")
        assert resp.status_code == 404

    @pytest.mark.parametrize("kind", [c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_missing_source_returns_400(self, client: AsyncClient, repo: Repository, kind: str):
        await _seed_list_metadata(repo, f"MG-{kind}-2", kind, ["A"])
        target_id = await _facet_id_by_name(client, kind, "A")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": target_id, "source_ids": [9999]})
        assert resp.status_code == 400

    @pytest.mark.parametrize("kind", [c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_target_missing_returns_404(self, client: AsyncClient, repo: Repository, kind: str):
        await _seed_list_metadata(repo, f"MG-{kind}-3", kind, ["A"])
        source_id = await _facet_id_by_name(client, kind, "A")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": 9999, "source_ids": [source_id]})
        assert resp.status_code == 404

    @pytest.mark.parametrize("kind", [c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_self_returns_400(self, client: AsyncClient, repo: Repository, kind: str):
        await _seed_list_metadata(repo, f"MG-{kind}-4", kind, ["A"])
        facet_id = await _facet_id_by_name(client, kind, "A")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": facet_id, "source_ids": [facet_id]})
        assert resp.status_code == 400


class TestMergeScalarFacet:
    @pytest.mark.parametrize(("kind", "field"), SCALAR_FACET_CASES, ids=[c[0] for c in SCALAR_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_updates_metadata_scalar(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        meta_a = await _seed_scalar_metadata(repo, f"MGS-{kind}-1a", kind, "A")
        meta_b = await _seed_scalar_metadata(repo, f"MGS-{kind}-1b", kind, "B")

        target_id = await _facet_id_by_name(client, kind, "A")
        source_id = await _facet_id_by_name(client, kind, "B")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": target_id, "source_ids": [source_id]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "A"
        assert data["count"] == 2

        for meta in (meta_a, meta_b):
            detail = await client.get(f"metadata/{meta.id}")
            assert detail.json()["metadata"][field] == "A"

        resp = await client.get(f"facets/{kind}/{source_id}")
        assert resp.status_code == 404


class TestMergeUserTag:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_moves_attachments(self, client: AsyncClient, repo: Repository):
        target = await repo.create_user_tag("target")
        source = await repo.create_user_tag("source")
        assert target.id is not None
        assert source.id is not None
        meta_a = await repo.upsert_metadata(number="MGU-1a")
        meta_b = await repo.upsert_metadata(number="MGU-1b")
        assert meta_a.id is not None
        assert meta_b.id is not None
        await repo.attach_user_tag(meta_a.id, target.id)
        await repo.attach_user_tag(meta_b.id, source.id)

        resp = await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [source.id]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "target"
        assert data["count"] == 2

        detail_b = await client.get(f"metadata/{meta_b.id}")
        assert any(t["name"] == "target" for t in detail_b.json()["user_tags"])

        resp = await client.get(f"facets/user_tag/{source.id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_skips_duplicate_attachment(self, client: AsyncClient, repo: Repository):
        """target 与 source 都挂载在同一 metadata 上时, 合并后不产生重复挂载."""
        target = await repo.create_user_tag("t")
        source = await repo.create_user_tag("s")
        assert target.id is not None
        assert source.id is not None
        meta = await repo.upsert_metadata(number="MGU-2")
        assert meta.id is not None
        await repo.attach_user_tag(meta.id, target.id)
        await repo.attach_user_tag(meta.id, source.id)

        resp = await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [source.id]})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        detail = await client.get(f"metadata/{meta.id}")
        tags = detail.json()["user_tags"]
        assert len(tags) == 1
        assert tags[0]["name"] == "t"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_missing_source_returns_400(self, client: AsyncClient, repo: Repository):
        target = await repo.create_user_tag("only")
        assert target.id is not None

        resp = await client.post("facets/user_tag/merge", json={"target_id": target.id, "source_ids": [9999]})
        assert resp.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_target_missing_returns_404(self, client: AsyncClient, repo: Repository):
        source = await repo.create_user_tag("orphan-source")
        assert source.id is not None

        resp = await client.post("facets/user_tag/merge", json={"target_id": 9999, "source_ids": [source.id]})
        assert resp.status_code == 404


class TestFacetRulesPersistence:
    """删除黑名单 / 合并别名压缩 - 重刮写入后仍生效."""

    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_blocks_and_survives_upsert(
        self, client: AsyncClient, repo: Repository, kind: str, field: str
    ):
        meta = await _seed_list_metadata(repo, f"BL-{kind}-1", kind, ["Alice", "Bob"])
        assert meta.id is not None
        facet_id = await _facet_id_by_name(client, kind, "Alice")

        resp = await client.delete(f"facets/{kind}/{facet_id}")
        assert resp.status_code == 204

        detail = await client.get(f"metadata/{meta.id}")
        assert "Alice" not in detail.json()["metadata"][field]
        assert "Bob" in detail.json()["metadata"][field]

        rules = await client.get(f"facets/{kind}/rules")
        assert rules.status_code == 200
        items = rules.json()["items"]
        assert any(r["source_name"] == "Alice" and r["action"] == "block" for r in items)

        # 模拟重刮带上原名
        if kind == "actor":
            await repo.upsert_metadata(number=meta.number, actors=["Alice", "Bob"])
        elif kind == "director":
            await repo.upsert_metadata(number=meta.number, directors=["Alice", "Bob"])
        else:
            await repo.upsert_metadata(number=meta.number, tags=["Alice", "Bob"])

        detail2 = await client.get(f"metadata/{meta.id}")
        values = detail2.json()["metadata"][field]
        assert "Alice" not in values
        assert "Bob" in values

        facets = await client.get(f"facets/{kind}?search=Alice")
        assert all(i["name"] != "Alice" for i in facets.json()["items"])

    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_compresses_alias_chain(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        meta = await _seed_list_metadata(repo, f"AL-{kind}-1", kind, ["A"])
        await _seed_list_metadata(repo, f"AL-{kind}-2", kind, ["B"])
        await _seed_list_metadata(repo, f"AL-{kind}-3", kind, ["C"])
        assert meta.id is not None

        id_a = await _facet_id_by_name(client, kind, "A")
        id_b = await _facet_id_by_name(client, kind, "B")
        id_c = await _facet_id_by_name(client, kind, "C")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": id_b, "source_ids": [id_a]})
        assert resp.status_code == 200
        resp = await client.post(f"facets/{kind}/merge", json={"target_id": id_c, "source_ids": [id_b]})
        assert resp.status_code == 200

        rules = (await client.get(f"facets/{kind}/rules")).json()["items"]
        if kind == "actor":
            # 演员别名已行化: 合并不动规则表, 名字并入 target 别名行
            assert rules == []
            resp = await client.get(f"actors/{id_c}")
            assert resp.json()["aliases"] == ["B", "A"]
        else:
            by_source = {r["source_name"]: r for r in rules}
            assert by_source["A"]["action"] == "alias"
            assert by_source["A"]["target_name"] == "C"
            assert by_source["B"]["action"] == "alias"
            assert by_source["B"]["target_name"] == "C"
            assert "A→B" not in {f"{r['source_name']}→{r['target_name']}" for r in rules if r["action"] == "alias"}

        if kind == "actor":
            await repo.upsert_metadata(number=meta.number, actors=["A", "Extra"])
        elif kind == "director":
            await repo.upsert_metadata(number=meta.number, directors=["A", "Extra"])
        else:
            await repo.upsert_metadata(number=meta.number, tags=["A", "Extra"])

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"][field] == ["C", "Extra"]

    @pytest.mark.parametrize(("kind", "field"), LIST_FACET_CASES, ids=[c[0] for c in LIST_FACET_CASES])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_block_collapses_inbound_aliases(self, client: AsyncClient, repo: Repository, kind: str, field: str):
        meta = await _seed_list_metadata(repo, f"BK-{kind}-1", kind, ["A"])
        await _seed_list_metadata(repo, f"BK-{kind}-2", kind, ["B"])
        assert meta.id is not None
        id_a = await _facet_id_by_name(client, kind, "A")
        id_b = await _facet_id_by_name(client, kind, "B")

        resp = await client.post(f"facets/{kind}/merge", json={"target_id": id_b, "source_ids": [id_a]})
        assert resp.status_code == 200

        # B 可能仍在; 重新取 id
        id_b2 = await _facet_id_by_name(client, kind, "B")
        resp = await client.delete(f"facets/{kind}/{id_b2}")
        assert resp.status_code == 204

        rules = (await client.get(f"facets/{kind}/rules")).json()["items"]
        by_source = {r["source_name"]: r for r in rules}
        assert by_source["A"]["action"] == "block"
        assert by_source["B"]["action"] == "block"

        if kind == "actor":
            await repo.upsert_metadata(number=meta.number, actors=["A", "B", "Keep"])
        elif kind == "director":
            await repo.upsert_metadata(number=meta.number, directors=["A", "B", "Keep"])
        else:
            await repo.upsert_metadata(number=meta.number, tags=["A", "B", "Keep"])

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"][field] == ["Keep"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_rule_allows_name_again(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="RULE-1", actors=["Alice"])
        assert meta.id is not None
        facet_id = await _facet_id_by_name(client, "actor", "Alice")
        assert (await client.delete(f"facets/actor/{facet_id}")).status_code == 204

        rules = (await client.get("facets/actor/rules")).json()["items"]
        rule = next(r for r in rules if r["source_name"] == "Alice")
        resp = await client.delete(f"facets/actor/rules/{rule['id']}")
        assert resp.status_code == 204

        await repo.upsert_metadata(number=meta.number, actors=["Alice"])
        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"]["actors"] == ["Alice"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_user_tag_delete_writes_no_rules(self, client: AsyncClient, repo: Repository):
        tag = await repo.create_user_tag("doomed")
        assert tag.id is not None
        resp = await client.delete(f"facets/user_tag/{tag.id}")
        assert resp.status_code == 204
        rules = await client.get("facets/user_tag/rules")
        assert rules.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_facet_not_found(self, client: AsyncClient):
        resp = await client.delete("facets/actor/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rename_swaps_display_name(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="RN-ALIAS-1", actors=["Old"])
        assert meta.id is not None
        facet_id = await _facet_id_by_name(client, "actor", "Old")
        resp = await client.patch(f"facets/actor/{facet_id}", json={"name": "New"})
        assert resp.status_code == 200

        # 改名不再写规则: 旧名入别名行, 重刮折回新展示名
        rules = (await client.get("facets/actor/rules")).json()["items"]
        assert not any(r["action"] == "alias" for r in rules)
        detail = await client.get(f"actors/{facet_id}")
        assert detail.json()["name"] == "New"
        assert detail.json()["aliases"] == ["Old"]

        await repo.upsert_metadata(number=meta.number, actors=["Old"])
        detail = await client.get(f"metadata/{meta.id}")
        assert detail.json()["metadata"]["actors"] == ["New"]


class TestActorMergePersonFields:
    """Actor 实体 merge 须携带人物元数据, 保留 target id."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_carries_person_metadata(self, client: AsyncClient, repo: Repository):
        from amane.db.actor_lookup import build_actor_lookup_names
        from amane.db.models import Actor

        await repo.upsert_metadata(number="ACT-PERSON-1", actors=["Canonical"])
        await repo.upsert_metadata(number="ACT-PERSON-2", actors=["OtherName"])

        target_id = await _facet_id_by_name(client, "actor", "Canonical")
        source_id = await _facet_id_by_name(client, "actor", "OtherName")

        async with repo._session() as session:
            source = await session.get(Actor, source_id)
            assert source is not None
            source.birthday = "1990-05-05"
            source.overview = "from-source"
            source.image_urls = ["http://img/a.jpg"]
            source.provider_ids = {"wikidata": "Q1"}
            session.add(source)
            await session.commit()
        await repo.save_actor(source, aliases=["Roma"])

        resp = await client.post("facets/actor/merge", json={"target_id": target_id, "source_ids": [source_id]})
        assert resp.status_code == 200
        assert resp.json()["id"] == target_id

        async with repo._session() as session:
            target = await session.get(Actor, target_id)
            assert target is not None
            assert target.name == "Canonical"
            assert target.birthday == "1990-05-05"
            assert target.overview == "from-source"
            assert target.image_urls == ["http://img/a.jpg"]
            assert target.provider_ids == {"wikidata": "Q1"}
            assert (await session.get(Actor, source_id)) is None

            names = await build_actor_lookup_names(session, target)
            assert names == ["Canonical", "OtherName", "Roma"]

        # 名字并入别名行, 不写别名规则
        assert await repo.get_actor_aliases(target_id) == ["OtherName", "Roma"]
        rules = (await client.get("facets/actor/rules")).json()["items"]
        assert not any(r["action"] == "alias" for r in rules)
