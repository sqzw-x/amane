"""分类索引 / 用户 tag / 评论 - repository 表测试."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlmodel import col, select

from amane.db.actor_lookup import build_actor_lookup_names
from amane.db.models import (
    Actor,
    ActorUserTag,
    FacetKind,
    FacetRuleAction,
    FacetSortField,
    Metadata,
    MetadataActor,
    SortOrder,
    UserTag,
)
from amane.db.repo_types import ActorBrowseParams
from amane.enums import ActorGender

if TYPE_CHECKING:
    from amane.db.repo_types import UserTagLinkAction
    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio


async def _tag(repo: Repository, name: str) -> UserTag:
    """测试便捷入口: 按名称取回或新建单个用户标签."""
    tags, _created = await repo.ensure_user_tags([name])
    return tags[0]


async def _tag_ids(repo: Repository, *names: str) -> list[int]:
    """按名称取回或新建标签并返回 id (测试里反复要非空 id)."""
    tags, _created = await repo.ensure_user_tags(list(names))
    ids = [tag.id for tag in tags]
    assert all(tag_id is not None for tag_id in ids)
    return [tag_id for tag_id in ids if tag_id is not None]


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
        tag = await _tag(repo, "watched")
        assert tag.id is not None
        await repo.apply_metadata_user_tags([meta.id], [tag.id], action="attach")
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
        tag = await _tag(repo, "fav")
        assert tag.id is not None

        first = await repo.apply_metadata_user_tags([meta.id], [tag.id], action="attach")
        assert first == (1, 0, 0)
        # 已挂载是幂等命中, 计入 unchanged, 不是错误
        again = await repo.apply_metadata_user_tags([meta.id], [tag.id], action="attach")
        assert again == (0, 1, 0)

        _, n = await repo.list_metadata(user_tag_ids=[tag.id])
        assert n == 1

        await repo.update_user_tag(tag.id, name="favorite")
        updated = await repo.get_user_tag(tag.id)
        assert updated is not None and updated.name == "favorite"
        assert await repo.delete_user_tag(tag.id) is True

    async def test_ensure_user_tags_reuses_existing(self, repo: Repository) -> None:
        """名称已存在时复用原行, 不新建也不报错; 返回与入参同序."""
        first, created = await repo.ensure_user_tags(["dup", "other"])
        assert created == 2
        again, created_again = await repo.ensure_user_tags(["other", "dup", "other"])
        assert created_again == 0
        assert [tag.id for tag in again] == [first[1].id, first[0].id]
        assert len(await repo.list_user_tags()) == 2

    async def test_ensure_user_tags_reuses_row_committed_by_concurrent_writer(self, repo: Repository) -> None:
        """他人先读到「不存在」、随后提交同名行时, 调用方复用该行而不是撞唯一索引."""
        async with repo._session() as other:
            other.add(UserTag(name="same"))
            await other.flush()  # 尚未提交: 并发调用方的 SELECT 看不到这一行
            task = asyncio.create_task(repo.ensure_user_tags(["same"]))
            await asyncio.sleep(0.1)  # 让调用方走完 SELECT 并卡在 INSERT 的写锁上
            await other.commit()
            tags, created = await task

        assert created == 0
        assert [tag.name for tag in tags] == ["same"]
        assert len(await repo.list_user_tags()) == 1

    async def test_comment_crud(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="CM-001")
        assert meta.id is not None
        c = await repo.create_comment(meta.id, "body1")
        assert c is not None and c.id is not None
        assert c.created_at == c.updated_at

        same = await repo.update_comment(c.id, body="body1")
        assert same is not None and same.updated_at == c.updated_at

        updated = await repo.update_comment(c.id, body="body2")
        assert updated is not None and updated.body == "body2"
        assert updated.updated_at > c.created_at

        assert await repo.delete_comment(c.id) is True
        assert await repo.create_comment(99999, "x") is None

    async def test_apply_unknown_tag_raises(self, repo: Repository) -> None:
        """未知标签 id 是请求级错误: 整请求不生效, 错误信息点名标签 id."""
        meta = await repo.upsert_metadata(number="UT-UNKNOWN")
        assert meta.id is not None
        tag = await _tag(repo, "known")
        assert tag.id is not None
        with pytest.raises(ValueError, match="用户标签不存在"):
            await repo.apply_metadata_user_tags([meta.id], [tag.id, 9999], action="attach")
        assert await repo.list_metadata_user_tags(meta.id) == []

    @pytest.mark.parametrize(
        ("ids", "action", "expected"),
        [
            ([9999], "attach", (0, 0, 1)),
            ([9999], "detach", (0, 0, 1)),
            ([], "attach", (0, 0, 0)),
        ],
    )
    async def test_apply_user_tags_missing_targets(
        self, repo: Repository, ids: list[int], action: UserTagLinkAction, expected: tuple[int, int, int]
    ) -> None:
        tag = await _tag(repo, "edge")
        assert tag.id is not None
        assert await repo.apply_metadata_user_tags(ids, [tag.id], action=action) == expected

    async def test_apply_many_tags_many_metadata(self, repo: Repository) -> None:
        """一行挂多个标签, 多行同挂: 计数以条目为单位."""
        t1 = await _tag(repo, "multi-1")
        t2 = await _tag(repo, "multi-2")
        assert t1.id is not None and t2.id is not None
        m1 = await repo.upsert_metadata(number="UT-MULTI-1")
        m2 = await repo.upsert_metadata(number="UT-MULTI-2")
        assert m1.id is not None and m2.id is not None

        assert await repo.apply_metadata_user_tags([m1.id, m2.id, 9999], [t1.id, t2.id], action="attach") == (2, 0, 1)
        assert [t.name for t in await repo.list_metadata_user_tags(m1.id)] == ["multi-1", "multi-2"]
        # 两行均已挂 t1, 因此都计入 unchanged
        assert await repo.apply_metadata_user_tags([m1.id, m2.id], [t1.id], action="attach") == (0, 2, 0)
        await repo.apply_metadata_user_tags([m1.id], [t1.id], action="detach")
        assert await repo.apply_metadata_user_tags([m1.id, m2.id], [t1.id], action="detach") == (1, 1, 0)
        assert [t.name for t in await repo.list_metadata_user_tags(m1.id)] == ["multi-2"]


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
        tag = await _tag(repo, "old")
        assert tag.id is not None
        renamed = await repo.rename_facet(FacetKind.USER_TAG, tag.id, "new")
        assert renamed is not None and renamed.name == "new"
        await _tag(repo, "taken")
        mine = await _tag(repo, "mine")
        assert mine.id is not None
        with pytest.raises(ValueError):
            await repo.rename_facet(FacetKind.USER_TAG, mine.id, "taken")
        assert await repo.rename_facet(FacetKind.USER_TAG, 9999, "x") is None

        target = await _tag(repo, "target")
        source = await _tag(repo, "source")
        assert target.id is not None and source.id is not None
        meta_a = await repo.upsert_metadata(number="MGU-1a")
        meta_b = await repo.upsert_metadata(number="MGU-1b")
        assert meta_a.id is not None and meta_b.id is not None
        await repo.apply_metadata_user_tags([meta_a.id], [target.id], action="attach")
        await repo.apply_metadata_user_tags([meta_b.id], [source.id], action="attach")
        merged = await repo.merge_facets(FacetKind.USER_TAG, target.id, [source.id])
        assert merged is not None and merged.name == "target" and merged.count == 2
        tags_b = await repo.list_metadata_user_tags(meta_b.id)
        assert any(t.name == "target" for t in tags_b)
        assert await repo.get_facet(FacetKind.USER_TAG, source.id) is None

        t2 = await _tag(repo, "t")
        s2 = await _tag(repo, "s")
        assert t2.id is not None and s2.id is not None
        meta = await repo.upsert_metadata(number="MGU-2")
        assert meta.id is not None
        await repo.apply_metadata_user_tags([meta.id], [t2.id, s2.id], action="attach")
        dup = await repo.merge_facets(FacetKind.USER_TAG, t2.id, [s2.id])
        assert dup is not None and dup.count == 1
        assert [t.name for t in await repo.list_metadata_user_tags(meta.id)] == ["t"]

        only = await _tag(repo, "only")
        assert only.id is not None
        with pytest.raises(ValueError):
            await repo.merge_facets(FacetKind.USER_TAG, only.id, [9999])
        orphan = await _tag(repo, "orphan-source")
        assert orphan.id is not None
        assert await repo.merge_facets(FacetKind.USER_TAG, 9999, [orphan.id]) is None

        doomed = await _tag(repo, "doomed")
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


class TestActorUserTags:
    async def test_apply_list_and_filter(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-001", actors=["Alice", "Bob", "Carol"])
        alice = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        bob = await _facet_id(repo, FacetKind.ACTOR, "Bob")
        carol = await _facet_id(repo, FacetKind.ACTOR, "Carol")
        fav_id, later_id = await _tag_ids(repo, "收藏", "稍后看")

        assert await repo.apply_actor_user_tags([alice, bob, 9999], [fav_id, later_id], action="attach") == (2, 0, 1)
        assert [t.name for t in await repo.list_actor_user_tags(alice)] == ["收藏", "稍后看"]
        # 挂载后即成为演员列表的筛选条件, 多值为 AND
        items, total = await repo.browse_actors(ActorBrowseParams(user_tag_ids=[fav_id]))
        assert total == 2 and {i.name for i in items} == {"Alice", "Bob"}
        items, total = await repo.browse_actors(ActorBrowseParams(user_tag_ids=[fav_id, later_id]))
        assert total == 2
        await repo.apply_actor_user_tags([bob], [later_id], action="detach")
        items, total = await repo.browse_actors(ActorBrowseParams(user_tag_ids=[fav_id, later_id]))
        assert total == 1 and items[0].name == "Alice"
        assert await repo.list_actor_user_tags(carol) == []

    async def test_apply_unknown_tag_raises(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-002", actors=["Alice"])
        alice = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        (tag_id,) = await _tag_ids(repo, "收藏")
        with pytest.raises(ValueError, match="用户标签不存在"):
            await repo.apply_actor_user_tags([alice], [tag_id, 9999], action="attach")
        assert await repo.list_actor_user_tags(alice) == []

    async def test_delete_user_tag_clears_actor_links(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-003", actors=["Alice"])
        alice = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        (tag_id,) = await _tag_ids(repo, "收藏")
        await repo.apply_actor_user_tags([alice], [tag_id], action="attach")
        assert await repo.delete_user_tag(tag_id) is True
        assert await repo.list_actor_user_tags(alice) == []

    async def test_delete_actor_clears_links(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-004", actors=["Alice"])
        alice = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        (tag_id,) = await _tag_ids(repo, "收藏")
        await repo.apply_actor_user_tags([alice], [tag_id], action="attach")
        assert await repo.delete_facet(FacetKind.ACTOR, alice) is True
        async with repo._session() as session:
            remaining = (await session.exec(select(ActorUserTag).where(col(ActorUserTag.actor_id) == alice))).all()
            assert list(remaining) == []

    async def test_merge_user_tags_carries_actor_links(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-005", actors=["Alice", "Bob"])
        alice = await _facet_id(repo, FacetKind.ACTOR, "Alice")
        bob = await _facet_id(repo, FacetKind.ACTOR, "Bob")
        target_id, source_id = await _tag_ids(repo, "target", "source")
        await repo.apply_actor_user_tags([alice], [target_id], action="attach")
        await repo.apply_actor_user_tags([bob], [source_id], action="attach")

        merged = await repo.merge_facets(FacetKind.USER_TAG, target_id, [source_id])
        assert merged is not None
        # 源标签的挂载迁入 target; 两个演员各有一条
        assert [t.name for t in await repo.list_actor_user_tags(bob)] == ["target"]

        # 同一个演员同时挂着两个待合并的标签时只保留一条
        t2_id, s2_id = await _tag_ids(repo, "t", "s")
        await repo.apply_actor_user_tags([alice], [t2_id, s2_id], action="attach")
        await repo.merge_facets(FacetKind.USER_TAG, t2_id, [s2_id])
        assert [t.name for t in await repo.list_actor_user_tags(alice)] == ["t", "target"]

    async def test_merge_actors_carries_user_tag_links(self, repo: Repository) -> None:
        await repo.upsert_metadata(number="AUT-006", actors=["Canonical", "Other"])
        target_id = await _facet_id(repo, FacetKind.ACTOR, "Canonical")
        source_id = await _facet_id(repo, FacetKind.ACTOR, "Other")
        shared_id, extra_id = await _tag_ids(repo, "shared", "extra")
        await repo.apply_actor_user_tags([target_id], [shared_id], action="attach")
        await repo.apply_actor_user_tags([source_id], [shared_id, extra_id], action="attach")

        assert await repo.merge_facets(FacetKind.ACTOR, target_id, [source_id]) is not None
        assert [t.name for t in await repo.list_actor_user_tags(target_id)] == ["extra", "shared"]
        async with repo._session() as session:
            orphans = (await session.exec(select(ActorUserTag).where(col(ActorUserTag.actor_id) == source_id))).all()
            assert list(orphans) == []
