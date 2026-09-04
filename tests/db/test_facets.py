"""分类索引 / 用户 tag / 评论 - repository 表测试."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from amane.db.actor_lookup import build_actor_lookup_names
from amane.db.models import Actor, FacetKind, FacetRuleAction, FacetSortField, Metadata, MetadataActor, SortOrder
from amane.enums import ActorGender

if TYPE_CHECKING:
    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio


class TestFacetSync:
    async def test_upsert_builds_actor_tag_studio_indexes(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(
            number="ABC-001",
            actors=["Alice", "Bob"],
            directors=["DirA"],
            tags=["tag1", "tag2"],
            studio="StudioX",
            publisher="PubY",
            series="SeriesZ",
        )
        assert meta.id is not None

        actors, total = await repo.list_facets(FacetKind.ACTOR)
        assert total == 2
        assert {a.name: a.count for a in actors} == {"Alice": 1, "Bob": 1}

        tags, _ = await repo.list_facets(FacetKind.TAG)
        assert {t.name for t in tags} == {"tag1", "tag2"}

        studios, _ = await repo.list_facets(FacetKind.STUDIO)
        assert studios[0].name == "StudioX" and studios[0].count == 1

    async def test_empty_actors_clears_junction(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="ABC-002", actors=["Alice"])
        assert meta.id is not None
        await repo.update_metadata(meta.id, actors=[])
        actors, total = await repo.list_facets(FacetKind.ACTOR)
        # 孤儿 Actor 保留, 但 count 为 0
        assert total == 1
        assert actors[0].count == 0
        items, n = await repo.list_metadata(actor_ids=[actors[0].id])
        assert n == 0 and items == []

    async def test_resolve_facet_ids_skips_missing_and_empty(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(
            number="ABC-009",
            actors=["Alice"],
            directors=["DirA"],
            tags=["tag1"],
            studio="StudioX",
            actor_genders={"Alice": ActorGender.FEMALE},
        )
        (
            actor_ids,
            actor_genders,
            director_ids,
            tag_ids,
            studio_id,
            publisher_id,
            series_id,
        ) = await repo.resolve_metadata_facet_ids(meta)
        assert set(actor_ids) == {"Alice"}
        assert actor_genders == {"Alice": ActorGender.FEMALE}
        assert set(director_ids) == {"DirA"}
        assert set(tag_ids) == {"tag1"}
        assert studio_id is not None
        assert publisher_id is None and series_id is None

        meta.actors = ["Alice", "Ghost"]
        meta.directors = []
        meta.tags = ["tag1", "missing-tag"]
        meta.studio = None
        actor_ids, actor_genders, director_ids, tag_ids, studio_id, _, _ = await repo.resolve_metadata_facet_ids(meta)
        assert set(actor_ids) == {"Alice"}
        assert "Ghost" not in actor_ids
        assert director_ids == {}
        assert set(tag_ids) == {"tag1"}
        assert studio_id is None

    async def test_unknown_facet_id_returns_empty(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="ABC-003", actors=["Alice"])
        items, n = await repo.list_metadata(actor_ids=[99999])
        assert n == 0 and items == []
        items, n = await repo.list_metadata(studio_ids=[99999])
        assert n == 0 and items == []

    async def test_scrape_upsert_preserves_user_tags_and_comments(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="ABC-004", actors=["Alice"], tags=["old"])
        assert meta.id is not None
        tag = await repo.create_user_tag("watched")
        assert tag.id is not None
        await repo.attach_user_tag(meta.id, tag.id)
        await repo.create_comment(meta.id, "hello")

        await repo.upsert_metadata(number="ABC-004", actors=["Bob"], tags=["new"])

        user_tags = await repo.list_metadata_user_tags(meta.id)
        assert [t.name for t in user_tags] == ["watched"]
        comments = await repo.list_comments(meta.id)
        assert [c.body for c in comments] == ["hello"]

        # 爬取 tags 已更新投影
        scraped, _ = await repo.list_facets(FacetKind.TAG)
        names = {t.name for t in scraped}
        assert "new" in names

    async def test_delete_metadata_keeps_actor_entity(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="ABC-005", actors=["KeepMe"])
        assert meta.id is not None
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        actor_id = actors[0].id
        await repo.delete_metadata(meta.id)
        facet = await repo.get_facet(FacetKind.ACTOR, actor_id)
        assert facet is not None
        assert facet.name == "KeepMe"
        assert facet.count == 0


class TestUserTagsAndComments:
    async def test_user_tag_crud_and_attach(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="UT-001")
        assert meta.id is not None
        tag = await repo.create_user_tag("fav")
        assert tag.id is not None
        assert await repo.attach_user_tag(meta.id, tag.id) is True
        assert await repo.attach_user_tag(meta.id, tag.id) is True  # 幂等
        _, n = await repo.list_metadata(user_tag_ids=[tag.id])
        assert n == 1
        assert await repo.detach_user_tag(meta.id, tag.id) is True
        assert await repo.detach_user_tag(meta.id, tag.id) is False
        await repo.update_user_tag(tag.id, name="favorite")
        updated = await repo.get_user_tag(tag.id)
        assert updated is not None and updated.name == "favorite"
        assert await repo.delete_user_tag(tag.id) is True

    async def test_duplicate_user_tag_name_raises(self, repo: Repository) -> None:
        await repo.create_user_tag("dup")
        with pytest.raises(IntegrityError):
            await repo.create_user_tag("dup")

    async def test_comment_crud(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="CM-001")
        assert meta.id is not None
        c = await repo.create_comment(meta.id, "body1")
        assert c is not None and c.id is not None
        updated = await repo.update_comment(c.id, body="body2")
        assert updated is not None and updated.body == "body2"
        assert await repo.delete_comment(c.id) is True
        assert await repo.create_comment(99999, "x") is None

    async def test_attach_missing_returns_false(self, repo: Repository) -> None:
        assert await repo.attach_user_tag(1, 1) is False

    async def test_batch_attach_detach_user_tag(self, repo: Repository) -> None:
        tag = await repo.create_user_tag("watched")
        assert tag.id is not None
        m1 = await repo.upsert_metadata(number="BT-001")
        m2 = await repo.upsert_metadata(number="BT-002")
        assert m1.id is not None and m2.id is not None

        affected, missing = await repo.batch_attach_user_tag([m1.id, m2.id, 9999], tag.id)
        assert (affected, missing) == (2, 1)
        again, _ = await repo.batch_attach_user_tag([m1.id], tag.id)
        assert again == 1
        none, all_miss = await repo.batch_attach_user_tag([m1.id], 9999)
        assert (none, all_miss) == (0, 1)

        det_ok, det_miss = await repo.batch_detach_user_tag([m1.id, m2.id], tag.id)
        assert (det_ok, det_miss) == (2, 0)
        # m2 已卸下, 再卸一次计入 missing
        _, again_miss = await repo.batch_detach_user_tag([m2.id], tag.id)
        assert again_miss == 1


class TestFacetFilterCombine:
    async def test_keyword_and_actor_filter(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="SSIS-001", title="Hello", actors=["Alice"])
        await repo.upsert_metadata(number="SSIS-002", title="World", actors=["Alice"])
        await repo.upsert_metadata(number="ABC-999", title="Hello", actors=["Bob"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR, search="Alice")
        alice_id = actors[0].id
        items, n = await repo.list_metadata(keyword="Hello", actor_ids=[alice_id])
        assert n == 1
        assert items[0].number == "SSIS-001"

    async def test_multi_actor_and(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="MA-001", actors=["Alice", "Bob"])
        await repo.upsert_metadata(number="MA-002", actors=["Alice"])
        await repo.upsert_metadata(number="MA-003", actors=["Bob", "Carol"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        by_name = {a.name: a.id for a in actors}
        items, n = await repo.list_metadata(actor_ids=[by_name["Alice"], by_name["Bob"]])
        assert n == 1 and items[0].number == "MA-001"

    async def test_multi_tag_and_with_actor(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="MT-001", actors=["Alice"], tags=["t1", "t2"])
        await repo.upsert_metadata(number="MT-002", actors=["Alice"], tags=["t1"])
        await repo.upsert_metadata(number="MT-003", actors=["Bob"], tags=["t1", "t2"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR, search="Alice")
        tags, _ = await repo.list_facets(FacetKind.TAG)
        by_tag = {t.name: t.id for t in tags}
        items, n = await repo.list_metadata(actor_ids=[actors[0].id], tag_ids=[by_tag["t1"], by_tag["t2"]])
        assert n == 1 and items[0].number == "MT-001"

    async def test_multi_studio_or(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="MS-001", studio="StudioA")
        await repo.upsert_metadata(number="MS-002", studio="StudioB")
        await repo.upsert_metadata(number="MS-003", studio="StudioC")
        studios, _ = await repo.list_facets(FacetKind.STUDIO)
        by_name = {s.name: s.id for s in studios}
        items, n = await repo.list_metadata(studio_ids=[by_name["StudioA"], by_name["StudioB"]])
        assert n == 2
        assert {i.number for i in items} == {"MS-001", "MS-002"}

    async def test_multi_studio_ignores_unknown_id(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="MS-010", studio="OnlyA")
        studios, _ = await repo.list_facets(FacetKind.STUDIO)
        items, n = await repo.list_metadata(studio_ids=[studios[0].id, 99999])
        assert n == 1 and items[0].number == "MS-010"


class TestFacetSort:
    @pytest.mark.parametrize(
        ("sort_by", "order", "expected"),
        [
            (FacetSortField.NAME, SortOrder.ASC, ["Alice", "Bob", "Carol"]),
            (FacetSortField.NAME, SortOrder.DESC, ["Carol", "Bob", "Alice"]),
            (FacetSortField.COUNT, SortOrder.ASC, ["Carol", "Bob", "Alice"]),
            (FacetSortField.COUNT, SortOrder.DESC, ["Alice", "Bob", "Carol"]),
        ],
    )
    async def test_link_facet_sort(
        self,
        repo: Repository,
        sort_by: FacetSortField,
        order: SortOrder,
        expected: list[str],
    ) -> None:
        await repo.upsert_metadata(number="FS-001", actors=["Alice", "Bob"])
        await repo.upsert_metadata(number="FS-002", actors=["Alice"])
        await repo.upsert_metadata(number="FS-003", actors=["Alice", "Bob", "Carol"])
        # Alice:3 Bob:2 Carol:1
        items, total = await repo.list_facets(FacetKind.ACTOR, sort_by=sort_by, order=order)
        assert total == 3
        assert [i.name for i in items] == expected

    @pytest.mark.parametrize(
        ("sort_by", "order", "expected"),
        [
            (FacetSortField.NAME, SortOrder.ASC, ["Alpha", "Beta", "Gamma"]),
            (FacetSortField.COUNT, SortOrder.DESC, ["Alpha", "Beta", "Gamma"]),
        ],
    )
    async def test_scalar_facet_sort(
        self,
        repo: Repository,
        sort_by: FacetSortField,
        order: SortOrder,
        expected: list[str],
    ) -> None:
        await repo.upsert_metadata(number="SS-001", studio="Alpha")
        await repo.upsert_metadata(number="SS-002", studio="Alpha")
        await repo.upsert_metadata(number="SS-003", studio="Beta")
        await repo.upsert_metadata(number="SS-004", studio="Gamma")
        # Alpha:2 Beta:1 Gamma:1 - count DESC ties break by id asc (Beta before Gamma)
        items, total = await repo.list_facets(FacetKind.STUDIO, sort_by=sort_by, order=order)
        assert total == 3
        assert [i.name for i in items] == expected

    async def test_sort_with_search(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="SR-001", actors=["Ann", "Bob"])
        await repo.upsert_metadata(number="SR-002", actors=["Ann", "Amy"])
        items, total = await repo.list_facets(
            FacetKind.ACTOR,
            search="A",
            sort_by=FacetSortField.COUNT,
            order=SortOrder.DESC,
        )
        assert total == 2
        assert [i.name for i in items] == ["Ann", "Amy"]


class TestOrphanActorRetention:
    async def test_junction_removed_but_row_remains(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="OR-001", actors=["Solo"])
        assert meta.id is not None
        await repo.update_metadata(meta.id, actors=[])
        # 直接查 Actor 表仍在
        from sqlmodel import select

        async with repo._session() as session:
            row = (await session.exec(select(Actor).where(Actor.name == "Solo"))).first()
            assert row is not None
            links = (await session.exec(select(MetadataActor).where(MetadataActor.actor_id == row.id))).all()
            assert links == []


_LIST_KINDS: tuple[tuple[FacetKind, str], ...] = (
    (FacetKind.ACTOR, "actors"),
    (FacetKind.DIRECTOR, "directors"),
    (FacetKind.TAG, "tags"),
)
_SCALAR_KINDS: tuple[tuple[FacetKind, str], ...] = (
    (FacetKind.STUDIO, "studio"),
    (FacetKind.PUBLISHER, "publisher"),
    (FacetKind.SERIES, "series"),
)


async def _facet_id(repo: Repository, kind: FacetKind, name: str) -> int:
    items, _ = await repo.list_facets(kind, search=name)
    fid = next(i.id for i in items if i.name == name)
    assert fid is not None
    return fid


async def _seed_list(repo: Repository, number: str, kind: FacetKind, values: list[str]):
    if kind == FacetKind.ACTOR:
        return await repo.upsert_metadata(number=number, actors=values)
    if kind == FacetKind.DIRECTOR:
        return await repo.upsert_metadata(number=number, directors=values)
    if kind == FacetKind.TAG:
        return await repo.upsert_metadata(number=number, tags=values)
    raise ValueError(kind)


async def _seed_scalar(repo: Repository, number: str, kind: FacetKind, value: str):
    if kind == FacetKind.STUDIO:
        return await repo.upsert_metadata(number=number, studio=value)
    if kind == FacetKind.PUBLISHER:
        return await repo.upsert_metadata(number=number, publisher=value)
    if kind == FacetKind.SERIES:
        return await repo.upsert_metadata(number=number, series=value)
    raise ValueError(kind)


def _list_names(meta: Metadata, kind: FacetKind) -> list[str]:
    if kind == FacetKind.ACTOR:
        return meta.actors
    if kind == FacetKind.DIRECTOR:
        return meta.directors
    if kind == FacetKind.TAG:
        return meta.tags
    raise ValueError(kind)


def _scalar_name(meta: Metadata, kind: FacetKind) -> str | None:
    if kind == FacetKind.STUDIO:
        return meta.studio
    if kind == FacetKind.PUBLISHER:
        return meta.publisher
    if kind == FacetKind.SERIES:
        return meta.series
    raise ValueError(kind)


class TestFacetRenameMergeDelete:
    """rename / merge / delete / 规则压缩: 语义在 repo, API 只测接线."""

    async def test_rename_and_conflict_list_kinds(self, repo: Repository) -> None:
        for kind, _field in _LIST_KINDS:
            meta = await _seed_list(repo, f"RN-{kind}-1", kind, ["Alice", "Carol"])
            assert meta.id is not None
            facet_id = await _facet_id(repo, kind, "Alice")
            renamed = await repo.rename_facet(kind, facet_id, "Renamed")
            assert renamed is not None and renamed.name == "Renamed"
            got = await repo.get_metadata(meta.id)
            assert got is not None
            assert _list_names(got, kind) == ["Renamed", "Carol"]

            same = await repo.rename_facet(kind, facet_id, "Renamed")
            assert same is not None and same.name == "Renamed"
            assert await repo.rename_facet(kind, 9999, "X") is None

            await _seed_list(repo, f"RN-{kind}-2a", kind, ["DupA"])
            await _seed_list(repo, f"RN-{kind}-2b", kind, ["DupB"])
            dup_id = await _facet_id(repo, kind, "DupA")
            with pytest.raises(ValueError):
                await repo.rename_facet(kind, dup_id, "DupB")

    async def test_rename_and_conflict_scalar_kinds(self, repo: Repository) -> None:
        for kind, _field in _SCALAR_KINDS:
            m1 = await _seed_scalar(repo, f"RS-{kind}-1a", kind, "Old")
            m2 = await _seed_scalar(repo, f"RS-{kind}-1b", kind, "Old")
            assert m1.id is not None and m2.id is not None
            facet_id = await _facet_id(repo, kind, "Old")
            renamed = await repo.rename_facet(kind, facet_id, "New")
            assert renamed is not None and renamed.name == "New" and renamed.count == 2
            for mid in (m1.id, m2.id):
                got = await repo.get_metadata(mid)
                assert got is not None
                assert _scalar_name(got, kind) == "New"
            await _seed_scalar(repo, f"RS-{kind}-2a", kind, "A")
            await _seed_scalar(repo, f"RS-{kind}-2b", kind, "B")
            aid = await _facet_id(repo, kind, "A")
            with pytest.raises(ValueError):
                await repo.rename_facet(kind, aid, "B")

    async def test_merge_list_kinds(self, repo: Repository) -> None:
        for kind, _field in _LIST_KINDS:
            meta_a = await _seed_list(repo, f"MG-{kind}-a", kind, ["A"])
            meta_b = await _seed_list(repo, f"MG-{kind}-b", kind, ["B"])
            meta_ab = await _seed_list(repo, f"MG-{kind}-c", kind, ["A", "B", "Other"])
            assert meta_a.id is not None and meta_b.id is not None and meta_ab.id is not None
            target_id = await _facet_id(repo, kind, "A")
            source_id = await _facet_id(repo, kind, "B")
            merged = await repo.merge_facets(kind, target_id, [source_id])
            assert merged is not None and merged.name == "A" and merged.count == 3
            for mid in (meta_a.id, meta_b.id, meta_ab.id):
                got = await repo.get_metadata(mid)
                assert got is not None
                values = _list_names(got, kind)
                assert values.count("A") == 1
                assert "B" not in values
            ab = await repo.get_metadata(meta_ab.id)
            assert ab is not None
            assert _list_names(ab, kind) == ["A", "Other"]
            assert await repo.get_facet(kind, source_id) is None
            with pytest.raises(ValueError):
                await repo.merge_facets(kind, target_id, [9999])
            with pytest.raises(ValueError):
                await repo.merge_facets(kind, target_id, [target_id])
            assert await repo.merge_facets(kind, 9999, [target_id]) is None

    async def test_merge_scalar_kinds(self, repo: Repository) -> None:
        for kind, _field in _SCALAR_KINDS:
            meta_a = await _seed_scalar(repo, f"MGS-{kind}-a", kind, "A")
            meta_b = await _seed_scalar(repo, f"MGS-{kind}-b", kind, "B")
            assert meta_a.id is not None and meta_b.id is not None
            target_id = await _facet_id(repo, kind, "A")
            source_id = await _facet_id(repo, kind, "B")
            merged = await repo.merge_facets(kind, target_id, [source_id])
            assert merged is not None and merged.name == "A" and merged.count == 2
            for mid in (meta_a.id, meta_b.id):
                got = await repo.get_metadata(mid)
                assert got is not None
                assert _scalar_name(got, kind) == "A"
            assert await repo.get_facet(kind, source_id) is None

    async def test_delete_blocks_and_alias_chain(self, repo: Repository) -> None:
        for kind, _field in _LIST_KINDS:
            meta = await _seed_list(repo, f"BL-{kind}-1", kind, ["Alice", "Bob"])
            assert meta.id is not None
            facet_id = await _facet_id(repo, kind, "Alice")
            assert await repo.delete_facet(kind, facet_id) is True
            got = await repo.get_metadata(meta.id)
            assert got is not None
            assert "Alice" not in _list_names(got, kind)
            assert "Bob" in _list_names(got, kind)
            rules = await repo.list_facet_rules(kind)
            assert any(r.source_name == "Alice" and r.action == FacetRuleAction.BLOCK for r in rules)
            await _seed_list(repo, meta.number, kind, ["Alice", "Bob"])
            again = await repo.get_metadata(meta.id)
            assert again is not None
            assert "Alice" not in _list_names(again, kind)
            items, _ = await repo.list_facets(kind, search="Alice")
            assert all(i.name != "Alice" for i in items)

            meta2 = await _seed_list(repo, f"AL-{kind}-1", kind, ["ChainA"])
            await _seed_list(repo, f"AL-{kind}-2", kind, ["ChainB"])
            await _seed_list(repo, f"AL-{kind}-3", kind, ["ChainC"])
            assert meta2.id is not None
            id_a = await _facet_id(repo, kind, "ChainA")
            id_b = await _facet_id(repo, kind, "ChainB")
            id_c = await _facet_id(repo, kind, "ChainC")
            assert await repo.merge_facets(kind, id_b, [id_a]) is not None
            assert await repo.merge_facets(kind, id_c, [id_b]) is not None
            if kind == FacetKind.ACTOR:
                assert not any(r.action == FacetRuleAction.ALIAS for r in await repo.list_facet_rules(kind))
                assert await repo.get_actor_aliases(id_c) == ["ChainB", "ChainA"]
            else:
                by_source = {r.source_name: r for r in await repo.list_facet_rules(kind)}
                assert by_source["ChainA"].action == FacetRuleAction.ALIAS
                assert by_source["ChainA"].target_name == "ChainC"
                assert by_source["ChainB"].target_name == "ChainC"
            await _seed_list(repo, meta2.number, kind, ["ChainA", "Extra"])
            after = await repo.get_metadata(meta2.id)
            assert after is not None
            assert _list_names(after, kind) == ["ChainC", "Extra"]

            meta3 = await _seed_list(repo, f"BK-{kind}-1", kind, ["BlkA"])
            await _seed_list(repo, f"BK-{kind}-2", kind, ["BlkB"])
            assert meta3.id is not None
            bid_a = await _facet_id(repo, kind, "BlkA")
            bid_b = await _facet_id(repo, kind, "BlkB")
            assert await repo.merge_facets(kind, bid_b, [bid_a]) is not None
            bid_b2 = await _facet_id(repo, kind, "BlkB")
            assert await repo.delete_facet(kind, bid_b2) is True
            blocked = {r.source_name: r for r in await repo.list_facet_rules(kind)}
            assert blocked["BlkA"].action == FacetRuleAction.BLOCK
            assert blocked["BlkB"].action == FacetRuleAction.BLOCK
            await _seed_list(repo, meta3.number, kind, ["BlkA", "BlkB", "Keep"])
            kept = await repo.get_metadata(meta3.id)
            assert kept is not None
            assert _list_names(kept, kind) == ["Keep"]

        assert await repo.delete_facet(FacetKind.ACTOR, 9999) is False

    async def test_user_tag_rename_merge_delete(self, repo: Repository) -> None:
        tag = await repo.create_user_tag("old")
        assert tag.id is not None
        renamed = await repo.rename_facet(FacetKind.USER_TAG, tag.id, "new")
        assert renamed is not None and renamed.name == "new"
        await repo.create_user_tag("taken")
        mine = await repo.create_user_tag("mine")
        assert mine.id is not None
        with pytest.raises(ValueError):
            await repo.rename_facet(FacetKind.USER_TAG, mine.id, "taken")
        assert await repo.rename_facet(FacetKind.USER_TAG, 9999, "x") is None

        target = await repo.create_user_tag("target")
        source = await repo.create_user_tag("source")
        assert target.id is not None and source.id is not None
        meta_a = await repo.upsert_metadata(number="MGU-1a")
        meta_b = await repo.upsert_metadata(number="MGU-1b")
        assert meta_a.id is not None and meta_b.id is not None
        await repo.attach_user_tag(meta_a.id, target.id)
        await repo.attach_user_tag(meta_b.id, source.id)
        merged = await repo.merge_facets(FacetKind.USER_TAG, target.id, [source.id])
        assert merged is not None and merged.name == "target" and merged.count == 2
        tags_b = await repo.list_metadata_user_tags(meta_b.id)
        assert any(t.name == "target" for t in tags_b)
        assert await repo.get_facet(FacetKind.USER_TAG, source.id) is None

        t2 = await repo.create_user_tag("t")
        s2 = await repo.create_user_tag("s")
        assert t2.id is not None and s2.id is not None
        meta = await repo.upsert_metadata(number="MGU-2")
        assert meta.id is not None
        await repo.attach_user_tag(meta.id, t2.id)
        await repo.attach_user_tag(meta.id, s2.id)
        dup = await repo.merge_facets(FacetKind.USER_TAG, t2.id, [s2.id])
        assert dup is not None and dup.count == 1
        assert [t.name for t in await repo.list_metadata_user_tags(meta.id)] == ["t"]

        only = await repo.create_user_tag("only")
        assert only.id is not None
        with pytest.raises(ValueError):
            await repo.merge_facets(FacetKind.USER_TAG, only.id, [9999])
        orphan = await repo.create_user_tag("orphan-source")
        assert orphan.id is not None
        assert await repo.merge_facets(FacetKind.USER_TAG, 9999, [orphan.id]) is None

        doomed = await repo.create_user_tag("doomed")
        assert doomed.id is not None
        assert await repo.delete_facet(FacetKind.USER_TAG, doomed.id) is True
        with pytest.raises(ValueError):
            await repo.list_facet_rules(FacetKind.USER_TAG)

    async def test_actor_rename_swaps_display_name(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="RN-ALIAS-1", actors=["Old"])
        assert meta.id is not None
        facet_id = await _facet_id(repo, FacetKind.ACTOR, "Old")
        renamed = await repo.rename_facet(FacetKind.ACTOR, facet_id, "New")
        assert renamed is not None and renamed.name == "New"
        rules = await repo.list_facet_rules(FacetKind.ACTOR)
        assert not any(r.action == FacetRuleAction.ALIAS for r in rules)
        assert await repo.get_actor_aliases(facet_id) == ["Old"]
        await repo.upsert_metadata(number=meta.number, actors=["Old"])
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.actors == ["New"]

    async def test_delete_rule_allows_name_again(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="RULE-1", actors=["Alice"])
        assert meta.id is not None
        facet_id = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        assert await repo.delete_facet(FacetKind.ACTOR, facet_id) is True
        rules = await repo.list_facet_rules(FacetKind.ACTOR)
        rule = next(r for r in rules if r.source_name == "Alice")
        assert rule.id is not None
        assert await repo.delete_facet_rule(FacetKind.ACTOR, rule.id) is True
        await repo.upsert_metadata(number=meta.number, actors=["Alice"])
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.actors == ["Alice"]

    async def test_actor_merge_carries_person_metadata(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="ACT-PERSON-1", actors=["Canonical"])
        await repo.upsert_metadata(number="ACT-PERSON-2", actors=["OtherName"])
        target_id = await _facet_id(repo, FacetKind.ACTOR, "Canonical")
        source_id = await _facet_id(repo, FacetKind.ACTOR, "OtherName")
        async with repo._session() as session:
            source = await session.get(Actor, source_id)
            assert source is not None
            source.birthday = "1990-05-05"
            source.overview = "from-source"
            source.image_urls = ["http://img/a.jpg"]
            source.provider_ids = {"wikidata": "Q1"}
            session.add(source)
            await session.commit()
        source = await repo.get_actor(source_id)
        assert source is not None
        await repo.save_actor(source, aliases=["Roma"])

        merged = await repo.merge_facets(FacetKind.ACTOR, target_id, [source_id])
        assert merged is not None and merged.id == target_id
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
        assert await repo.get_actor_aliases(target_id) == ["OtherName", "Roma"]
        rules = await repo.list_facet_rules(FacetKind.ACTOR)
        assert not any(r.action == FacetRuleAction.ALIAS for r in rules)
