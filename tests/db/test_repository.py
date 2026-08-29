import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from amane.db.models import (
    FacetKind,
    FacetRuleAction,
    MediaFileStatus,
    MediaSortField,
    MetadataSortField,
    RoutineType,
    SortOrder,
    TaskSortField,
    TaskStatus,
    TaskType,
)
from amane.db.repo_types import _MEDIA_SORT_COLUMNS, _METADATA_SORT_COLUMNS, _TASK_SORT_COLUMNS, ActorBrowseParams
from amane.enums import LibraryAutomation
from amane.organize.path_templates import VIDEO_TEMPLATE_DEFAULT
from amane.parsing import ContentType, Mosaic
from tests.helpers import assert_exhaustive_enum

if TYPE_CHECKING:
    from amane.db.repository import Repository


@pytest.mark.parametrize(
    "sort_map,field_enum",
    [
        (_MEDIA_SORT_COLUMNS, MediaSortField),
        (_TASK_SORT_COLUMNS, TaskSortField),
    ],
)
def test_sort_field_map_exhaustive(sort_map, field_enum):
    assert_exhaustive_enum(sort_map.keys(), field_enum, f"{field_enum.__name__} not fully covered")


def test_metadata_sort_field_map_covers_columns():
    """FILE_COUNT 走关联计数表达式, 不进列映射; 其余排序字段必须有列."""
    assert set(_METADATA_SORT_COLUMNS) == set(MetadataSortField) - {MetadataSortField.FILE_COUNT}


class TestMediaFileRepo:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_get(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/ABC-123.mp4", number="ABC-123")
        assert media.id is not None
        assert media.status == MediaFileStatus.PENDING

        fetched = await repo.get_media_file(media.id)
        assert fetched is not None
        assert fetched.path == "/video/ABC-123.mp4"
        assert fetched.content_type is ContentType.CENSORED
        assert fetched.has_subtitle is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_by_path(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/video/ABC-123.mp4", number="ABC-123")
        found = await repo.get_media_file_by_path("/video/ABC-123.mp4")
        assert found is not None
        assert found.number == "ABC-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_by_path_not_found(self, repo: Repository):
        assert await repo.get_media_file_by_path("/nonexistent") is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_status(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/X.mp4")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED)
        fetched = await repo.get_media_file(media.id)
        assert fetched is not None
        assert fetched.status == MediaFileStatus.SCRAPED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_by_status(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/a.mp4")
        await repo.create_media_file(library_id=1, path="/b.mp4")
        m3 = await repo.create_media_file(library_id=1, path="/c.mp4")
        assert m3.id is not None
        await repo.update_media_file(m3.id, status=MediaFileStatus.SCRAPED)

        pending = await repo.list_media_files(status=[MediaFileStatus.PENDING])
        assert len(pending) == 2
        scraped = await repo.list_media_files(status=[MediaFileStatus.SCRAPED])
        assert len(scraped) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_existing_paths_empty(self, repo: Repository):
        result = await repo.get_valid([])
        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_existing_paths(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/video/A.mp4")
        await repo.create_media_file(library_id=1, path="/video/B.mp4")

        result = await repo.get_valid(["/video/A.mp4", "/video/C.mp4", "/video/B.mp4"])
        assert {f.path for f in result} == {"/video/A.mp4", "/video/B.mp4"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_invalid_scoped_to_library(self, repo: Repository):
        """失效清理按 library_id 收窄, 不把其它库的文件当成无效."""
        lib_a = await repo.create_library(name="a", path="/a")
        lib_b = await repo.create_library(name="b", path="/b")
        assert lib_a.id is not None and lib_b.id is not None
        await repo.create_media_file(library_id=lib_a.id, path="/a/1.mp4")
        gone = await repo.create_media_file(library_id=lib_a.id, path="/a/missing.mp4")
        keep_b = await repo.create_media_file(library_id=lib_b.id, path="/b/1.mp4")
        assert gone.id is not None and keep_b.id is not None

        invalid = await repo.get_invalid(["/a/1.mp4"], library_id=lib_a.id)
        assert {f.id for f in invalid} == {gone.id}
        assert await repo.get_media_file(keep_b.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_media_file(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/OLD.mp4", number="OLD-001")
        assert media.id is not None

        updated = await repo.update_media_file(media.id, path="/video/NEW.mp4", number="NEW-001", size=1024)
        assert updated is not None
        assert updated.path == "/video/NEW.mp4"
        assert updated.number == "NEW-001"
        assert updated.size == 1024

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_media_file_parses_phase_from_path(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/media/MIDV-123-UC-4K.mp4")
        assert media.content_type is ContentType.CENSORED
        assert media.mosaic is Mosaic.UNCENSORED
        assert media.has_subtitle is True
        assert media.definition == "4K"

        heyzo = await repo.create_media_file(library_id=1, path="/media/HEYZO-1234.mp4")
        assert heyzo.content_type is ContentType.UNCENSORED
        assert heyzo.mosaic is None
        assert heyzo.has_subtitle is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_path_recomputes_phase(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/MIDV-123.mp4")
        assert media.id is not None
        assert media.mosaic is None
        updated = await repo.update_media_file(media.id, path="/video/MIDV-123-U.mp4")
        assert updated is not None
        assert updated.mosaic is Mosaic.UNCENSORED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_media_files_phase_filters(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/v/MIDV-001-C.mp4")
        await repo.create_media_file(library_id=1, path="/v/MIDV-002-U.mp4")
        await repo.create_media_file(library_id=1, path="/v/HEYZO-1234.mp4")
        await repo.create_media_file(library_id=1, path="/v/MIDV-003-破解.mp4")

        subs = await repo.list_media_files(has_subtitle=True, limit=None)
        assert {f.path for f in subs} == {"/v/MIDV-001-C.mp4"}
        uncensored = await repo.list_media_files(uncensored=True, limit=None)
        assert {f.path for f in uncensored} == {"/v/MIDV-002-U.mp4", "/v/HEYZO-1234.mp4"}
        cracked = await repo.list_media_files(mosaic=Mosaic.CRACKED, limit=None)
        assert [f.path for f in cracked] == ["/v/MIDV-003-破解.mp4"]
        heyzo = await repo.list_media_files(content_type=ContentType.UNCENSORED, limit=None)
        assert [f.path for f in heyzo] == ["/v/HEYZO-1234.mp4"]
        assert await repo.count_media_files(uncensored=True) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_media_file_not_found(self, repo: Repository):
        result = await repo.update_media_file(9999, path="/x.mp4")
        assert result is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_media_file_not_found(self, repo: Repository):
        result = await repo.delete_media_file(9999)
        assert result is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_media_files_with_search(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/video/MIDV-123.mp4", number="MIDV-123")
        await repo.create_media_file(library_id=1, path="/video/ABC-456.mp4", number="ABC-456")

        result = await repo.list_media_files(search="MIDV")
        assert len(result) == 1
        assert result[0].number == "MIDV-123"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_media_files_search_by_path(self, repo: Repository):
        await repo.create_media_file(library_id=1, path="/video/MIDV-123.mp4", number="MIDV-123")
        await repo.create_media_file(library_id=1, path="/other/ABC-456.mp4", number="ABC-456")

        result = await repo.list_media_files(search="other")
        assert len(result) == 1
        assert result[0].path == "/other/ABC-456.mp4"

    @pytest.mark.parametrize(
        "status_filter,search,expected_count",
        [
            (None, None, 3),
            ([MediaFileStatus.PENDING], None, 2),
            ([MediaFileStatus.SCRAPED], None, 1),
            (None, "ABC", 2),
            ([MediaFileStatus.PENDING], "ABC", 2),
        ],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_count_media_files(self, repo: Repository, status_filter, search, expected_count):
        await repo.create_media_file(library_id=1, path="/video/ABC-123.mp4", number="ABC-123")
        await repo.create_media_file(library_id=1, path="/video/ABC-456.mp4", number="ABC-456")
        m3 = await repo.create_media_file(library_id=1, path="/video/XYZ-789.mp4", number="XYZ-789")
        assert m3.id is not None
        await repo.update_media_file(m3.id, status=MediaFileStatus.SCRAPED)

        count = await repo.count_media_files(status=status_filter, search=search)
        assert count == expected_count


class TestMetadataRepo:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_upsert_and_get(self, repo: Repository):
        meta = await repo.upsert_metadata(
            number="MIDV-123",
            title="Test Title",
            actors=["A", "B"],
            tags=["tag1"],
        )
        assert meta.id is not None

        fetched = await repo.get_metadata(meta.id)
        assert fetched is not None
        assert fetched.title == "Test Title"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_by_number(self, repo: Repository):
        await repo.upsert_metadata(number="MIDV-123", title="X")
        found = await repo.get_metadata_by_number("MIDV-123")
        assert found is not None
        assert found.title == "X"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_by_number_case_insensitive(self, repo: Repository):
        await repo.upsert_metadata(number="MIDV-123", title="X")
        found = await repo.get_metadata_by_number("midv-123")
        assert found is not None
        assert found.number == "MIDV-123"
        assert found.title == "X"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upsert_update(self, repo: Repository):
        await repo.upsert_metadata(number="MIDV-123", title="Old")
        await repo.upsert_metadata(number="MIDV-123", title="New", actors=["C"])
        found = await repo.get_metadata_by_number("MIDV-123")
        assert found is not None
        assert found.title == "New"
        assert found.actors == ["C"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upsert_cleans_actor_alias_names(self, repo: Repository):
        meta = await repo.upsert_metadata(
            number="MIDV-123", title="X", actors=["河北彩花（河北彩伽）", "三上悠亜(みかみ ゆあ, Mikami Yua)"]
        )
        # 真值只留展示名
        assert meta.actors == ["河北彩花", "三上悠亜"]

        actors = await repo.get_actors_by_names(["河北彩花", "三上悠亜"])
        aliases_by_name: dict[str, list[str]] = {}
        for a in actors:
            assert a.id is not None
            aliases_by_name[a.name] = await repo.get_actor_aliases(a.id)
        assert aliases_by_name["河北彩花"] == ["河北彩伽"]
        assert aliases_by_name["三上悠亜"] == ["みかみ ゆあ", "Mikami Yua"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upsert_actor_alias_clean_idempotent(self, repo: Repository):
        await repo.upsert_metadata(number="MIDV-123", actors=["A（B）"])
        await repo.upsert_metadata(number="MIDV-123", actors=["A（B）"], title="Y")

        meta = await repo.get_metadata_by_number("MIDV-123")
        assert meta is not None
        assert meta.actors == ["A"]
        actors = await repo.get_actors_by_names(["A"])
        # 重刮不重复入表
        assert actors[0].id is not None
        assert await repo.get_actor_aliases(actors[0].id) == ["B"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_actor_alias_clean_merges_existing_aliases(self, repo: Repository):
        await repo.upsert_metadata(number="MIDV-001", actors=["河北彩花"])
        actors = await repo.get_actors_by_names(["河北彩花"])
        assert actors[0].id is not None
        # 档案刮削已写入的别名保留, 影片侧清洗追加在末尾
        await repo.update_actor(actors[0].id, aliases=["Mikami Yua"])

        await repo.upsert_metadata(number="MIDV-002", actors=["河北彩花（河北彩伽）"])

        actors = await repo.get_actors_by_names(["河北彩花"])
        assert actors[0].id is not None
        assert await repo.get_actor_aliases(actors[0].id) == ["Mikami Yua", "河北彩伽"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_metadata_cleans_actor_alias_names(self, repo: Repository):
        meta = await repo.upsert_metadata(number="MIDV-123", title="X")
        assert meta.id is not None
        updated = await repo.update_metadata(meta.id, actors=["A（B、C）"])
        assert updated is not None
        assert updated.actors == ["A"]
        actors = await repo.get_actors_by_names(["A"])
        assert actors[0].id is not None
        assert await repo.get_actor_aliases(actors[0].id) == ["B", "C"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_alias_clean_targets_renamed_actor(self, repo: Repository):
        # 改名后旧展示名入别名行; 重刮同名 (含括号别名) 折回改名后的实体, 不留孤儿实体.
        await repo.upsert_metadata(number="MIDV-001", actors=["A"])
        facets, _ = await repo.list_facets(FacetKind.ACTOR, search="A")
        assert facets[0].name == "A"
        await repo.rename_facet(FacetKind.ACTOR, facets[0].id, "C")

        meta = await repo.upsert_metadata(number="MIDV-002", actors=["A（B）"])

        assert meta.actors == ["C"]
        assert await repo.get_actors_by_names(["A"]) == []
        actors = await repo.get_actors_by_names(["C"])
        assert actors[0].id is not None
        assert await repo.get_actor_aliases(actors[0].id) == ["A", "B"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_bare_alias_name_folds_to_display(self, repo: Repository):
        """站点给的裸别名 (无括号) 也折到已认定演员, 不再创建重复实体."""
        await repo.upsert_metadata(number="MIDV-001", actors=["A"])
        actor = (await repo.get_actors_by_names(["A"]))[0]
        assert actor.id is not None
        await repo.update_actor(actor.id, aliases=["OldName"])

        meta = await repo.upsert_metadata(number="MIDV-002", actors=["OldName"])
        assert meta.actors == ["A"]
        assert await repo.get_actors_by_names(["OldName"]) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_display_switch_to_existing_alias(self, repo: Repository):
        """展示名可切换到已有别名: 旧展示名入表, 新展示名出表, 存量影片改写."""
        await repo.upsert_metadata(number="MIDV-001", actors=["Canonical"])
        await repo.upsert_metadata(number="MIDV-002", actors=["Canonical"])
        actor = (await repo.get_actors_by_names(["Canonical"]))[0]
        assert actor.id is not None
        await repo.update_actor(actor.id, aliases=["Preferred", "Alt"])

        await repo.rename_facet(FacetKind.ACTOR, actor.id, "Preferred")

        items, _ = await repo.list_facets(FacetKind.ACTOR)
        names = {i.name for i in items}
        assert "Canonical" not in names and "Preferred" in names
        alias_names = await repo.get_actor_aliases(actor.id)
        assert alias_names == ["Alt", "Canonical"]
        metas = await repo.get_metadata_by_number("MIDV-001")
        assert metas is not None
        assert metas.actors == ["Preferred"]
        # 重刮旧名折回新展示名
        meta2 = await repo.upsert_metadata(number="MIDV-003", actors=["Canonical"])
        assert meta2.actors == ["Preferred"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_shared_alias_resolves_both_and_keeps_ambiguity(self, repo: Repository):
        """同名别名属于两个演员: 搜索命中两者, 裸名写入保持名字本身为新实体 (可再合并)."""
        await repo.upsert_metadata(number="S-1", actors=["One"])
        await repo.upsert_metadata(number="S-2", actors=["Two"])
        one = (await repo.get_actors_by_names(["One"]))[0]
        two = (await repo.get_actors_by_names(["Two"]))[0]
        assert one.id is not None and two.id is not None
        await repo.update_actor(one.id, aliases=["共享名"])
        await repo.update_actor(two.id, aliases=["共享名"])

        items, total = await repo.browse_actors(ActorBrowseParams(search="共享名"))
        assert total == 2
        assert {i.name for i in items} == {"One", "Two"}

        # 歧义时以名字本身为展示名 (确定性, 可再合并)
        meta = await repo.upsert_metadata(number="S-3", actors=["共享名"])
        assert meta.actors == ["共享名"]
        assert (await repo.get_actors_by_names(["共享名"])) != []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_blocks_own_names_keeps_shared(self, repo: Repository):
        """删除演员拉黑其独有名 (展示名+别名); 共享别名不拉黑, 另一演员仍可解析."""
        await repo.upsert_metadata(number="D-1", actors=["One", "Two"])
        one = (await repo.get_actors_by_names(["One"]))[0]
        two = (await repo.get_actors_by_names(["Two"]))[0]
        assert one.id is not None and two.id is not None
        await repo.update_actor(one.id, aliases=["独有名", "共享名"])
        await repo.update_actor(two.id, aliases=["共享名"])

        await repo.delete_facet(FacetKind.ACTOR, one.id)

        meta = await repo.upsert_metadata(number="D-2", actors=["独有名", "共享名", "OK"])
        assert meta.actors == ["Two", "OK"]
        rules = await repo.list_facet_rules(FacetKind.ACTOR)
        by_source = {r.source_name for r in rules}
        assert {"One", "独有名"} <= by_source
        assert all(r.action == "block" for r in rules if r.source_name in by_source)
        assert "共享名" not in by_source

    @pytest.mark.asyncio(loop_scope="function")
    async def test_actor_alias_rule_write_guard(self, repo: Repository):
        """规则唯一写点拒绝 (actor, alias); rename 走别名行, 不写规则 (见 rename 用例)."""
        from amane.db.repos.facet_helpers import _set_facet_rule

        async with repo._session() as session:
            with pytest.raises(ValueError, match="actor_aliases 表取代"):
                await _set_facet_rule(session, FacetKind.ACTOR, "X", FacetRuleAction.ALIAS, "Y")
        assert await repo.list_facet_rules(FacetKind.ACTOR) == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upsert_case_insensitive_preserves_number(self, repo: Repository):
        first = await repo.upsert_metadata(number="abc-001", title="Old")
        second = await repo.upsert_metadata(number="ABC-001", title="New")
        assert second.id == first.id
        assert second.number == "abc-001"
        assert second.title == "New"
        items, total = await repo.list_metadata()
        assert total == 1
        assert items[0].number == "abc-001"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_empty(self, repo: Repository):
        items, total = await repo.list_metadata()
        assert items == []
        assert total == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata(self, repo: Repository):
        await repo.upsert_metadata(number="ABC-001", title="First")
        await repo.upsert_metadata(number="ABC-002", title="Second")
        await repo.upsert_metadata(number="XYZ-999", title="Third")

        items, total = await repo.list_metadata()
        assert total == 3
        assert len(items) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_with_keyword(self, repo: Repository):
        await repo.upsert_metadata(number="ABC-001", title="Alpha")
        await repo.upsert_metadata(number="ABC-002", title="Beta")
        await repo.upsert_metadata(number="XYZ-999", title="Alpha")

        items, total = await repo.list_metadata(keyword="Alpha")
        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_keyword_by_number(self, repo: Repository):
        await repo.upsert_metadata(number="ABC-001", title="First")
        await repo.upsert_metadata(number="XYZ-999", title="Second")

        items, total = await repo.list_metadata(keyword="ABC")
        assert total == 1
        assert items[0].number == "ABC-001"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_pagination(self, repo: Repository):
        for i in range(5):
            await repo.upsert_metadata(number=f"NUM-{i:03d}", title=f"Title {i}")

        items, total = await repo.list_metadata(offset=0, limit=2)
        assert total == 5
        assert len(items) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_updated_before(self, repo: Repository):
        before = datetime.now(UTC)
        await repo.upsert_metadata(number="ABC-001")

        # 门槛早于插入时间 → 空; 晚于 → 全命中
        items, total = await repo.list_metadata(updated_before=before)
        assert total == 0
        items, total = await repo.list_metadata(updated_before=datetime.now(UTC) + timedelta(seconds=1))
        assert total == 1
        assert items[0].number == "ABC-001"

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("has_files", "expected_numbers"),
        [
            (True, ["HAS-1", "HAS-2"]),
            (False, ["NONE-1"]),
            (None, ["HAS-1", "HAS-2", "NONE-1"]),
        ],
    )
    async def test_list_metadata_has_files(self, repo: Repository, has_files: bool | None, expected_numbers: list[str]):
        has1 = await repo.upsert_metadata(number="HAS-1", title="With one")
        has2 = await repo.upsert_metadata(number="HAS-2", title="With two")
        await repo.upsert_metadata(number="NONE-1", title="Orphan")
        assert has1.id is not None and has2.id is not None

        m1 = await repo.create_media_file(library_id=1, path="/v/has1.mp4", number="HAS-1")
        assert m1.id is not None
        await repo.update_media_file(m1.id, metadata_id=has1.id)
        m2a = await repo.create_media_file(library_id=1, path="/v/has2a.mp4", number="HAS-2")
        m2b = await repo.create_media_file(library_id=1, path="/v/has2b.mp4", number="HAS-2")
        assert m2a.id is not None and m2b.id is not None
        await repo.update_media_file(m2a.id, metadata_id=has2.id)
        await repo.update_media_file(m2b.id, metadata_id=has2.id)
        # 未关联 metadata 的文件不应影响筛选
        await repo.create_media_file(library_id=1, path="/v/orphan-file.mp4", number="ORPHAN")

        items, total = await repo.list_metadata(
            has_files=has_files, sort_by=MetadataSortField.NUMBER, order=SortOrder.ASC
        )
        assert total == len(expected_numbers)
        assert [m.number for m in items] == expected_numbers

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("order", "expected_numbers"),
        [
            (SortOrder.ASC, ["NONE", "ONE", "TWO"]),
            (SortOrder.DESC, ["TWO", "ONE", "NONE"]),
        ],
    )
    async def test_list_metadata_sort_by_file_count(
        self, repo: Repository, order: SortOrder, expected_numbers: list[str]
    ):
        none = await repo.upsert_metadata(number="NONE", title="Zero")
        one = await repo.upsert_metadata(number="ONE", title="One")
        two = await repo.upsert_metadata(number="TWO", title="Two")
        assert none.id is not None and one.id is not None and two.id is not None

        m1 = await repo.create_media_file(library_id=1, path="/v/one.mp4", number="ONE")
        assert m1.id is not None
        await repo.update_media_file(m1.id, metadata_id=one.id)
        m2a = await repo.create_media_file(library_id=1, path="/v/two-a.mp4", number="TWO")
        m2b = await repo.create_media_file(library_id=1, path="/v/two-b.mp4", number="TWO")
        assert m2a.id is not None and m2b.id is not None
        await repo.update_media_file(m2a.id, metadata_id=two.id)
        await repo.update_media_file(m2b.id, metadata_id=two.id)

        items, total = await repo.list_metadata(sort_by=MetadataSortField.FILE_COUNT, order=order)
        assert total == 3
        assert [m.number for m in items] == expected_numbers

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_has_files_with_keyword(self, repo: Repository):
        linked = await repo.upsert_metadata(number="LINK-1", title="Alpha Linked")
        await repo.upsert_metadata(number="NONE-1", title="Alpha Orphan")
        await repo.upsert_metadata(number="OTHER", title="Beta Linked")
        assert linked.id is not None
        media = await repo.create_media_file(library_id=1, path="/v/link.mp4", number="LINK-1")
        assert media.id is not None
        await repo.update_media_file(media.id, metadata_id=linked.id)

        items, total = await repo.list_metadata(keyword="Alpha", has_files=True)
        assert total == 1
        assert items[0].number == "LINK-1"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_count_media_by_metadata_ids(self, repo: Repository):
        a = await repo.upsert_metadata(number="CNT-A", title="A")
        b = await repo.upsert_metadata(number="CNT-B", title="B")
        c = await repo.upsert_metadata(number="CNT-C", title="C")
        assert a.id is not None and b.id is not None and c.id is not None

        ma = await repo.create_media_file(library_id=1, path="/v/a.mp4", number="CNT-A")
        assert ma.id is not None
        await repo.update_media_file(ma.id, metadata_id=a.id)
        mb1 = await repo.create_media_file(library_id=1, path="/v/b1.mp4", number="CNT-B")
        mb2 = await repo.create_media_file(library_id=1, path="/v/b2.mp4", number="CNT-B")
        assert mb1.id is not None and mb2.id is not None
        await repo.update_media_file(mb1.id, metadata_id=b.id)
        await repo.update_media_file(mb2.id, metadata_id=b.id)

        counts = await repo.count_media_by_metadata_ids([a.id, b.id, c.id])
        assert counts == {a.id: 1, b.id: 2}
        assert await repo.count_media_by_metadata_ids([]) == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_metadata_file_phase_filters(self, repo: Repository):
        sub = await repo.upsert_metadata(number="SUB-1")
        u_file = await repo.upsert_metadata(number="U-1")
        heyzo = await repo.upsert_metadata(number="HEYZO-1")
        plain = await repo.upsert_metadata(number="PLAIN-1")
        assert sub.id and u_file.id and heyzo.id and plain.id

        m_sub = await repo.create_media_file(library_id=1, path="/v/MIDV-001-C.mp4")
        m_u = await repo.create_media_file(library_id=1, path="/v/MIDV-002-U.mp4")
        m_h = await repo.create_media_file(library_id=1, path="/v/HEYZO-1234.mp4")
        m_p = await repo.create_media_file(library_id=1, path="/v/MIDV-003.mp4")
        assert m_sub.id and m_u.id and m_h.id and m_p.id
        await repo.update_media_file(m_sub.id, metadata_id=sub.id)
        await repo.update_media_file(m_u.id, metadata_id=u_file.id)
        await repo.update_media_file(m_h.id, metadata_id=heyzo.id)
        await repo.update_media_file(m_p.id, metadata_id=plain.id)

        items, total = await repo.list_metadata(has_subtitle=True)
        assert total == 1
        assert items[0].number == "SUB-1"
        items, total = await repo.list_metadata(uncensored=True, sort_by=MetadataSortField.NUMBER, order=SortOrder.ASC)
        assert [m.number for m in items] == ["HEYZO-1", "U-1"]
        items, total = await repo.list_metadata(uncensored=False, sort_by=MetadataSortField.NUMBER, order=SortOrder.ASC)
        assert [m.number for m in items] == ["PLAIN-1", "SUB-1"]
        items, total = await repo.list_metadata(content_type=ContentType.UNCENSORED)
        assert total == 1
        assert items[0].number == "HEYZO-1"

        summaries = await repo.summarize_media_by_metadata_ids([sub.id, u_file.id, heyzo.id, plain.id])
        assert summaries[sub.id].file_count == 1
        assert summaries[sub.id].phase.has_subtitle is True
        assert summaries[heyzo.id].phase.uncensored is True
        assert summaries[u_file.id].phase.uncensored is True
        assert summaries[plain.id].phase.uncensored is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_metadata(self, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-001", title="Old Title", actors=["A"])
        assert meta.id is not None

        updated = await repo.update_metadata(meta.id, title="New Title", plot="A plot")
        assert updated is not None
        assert updated.title == "New Title"
        assert updated.plot == "A plot"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_metadata_not_found(self, repo: Repository):
        result = await repo.update_metadata(9999, title="X")
        assert result is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_metadata(self, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-001", title="Test")
        assert meta.id is not None

        result = await repo.delete_metadata(meta.id)
        assert result is True
        assert await repo.get_metadata(meta.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_metadata_not_found(self, repo: Repository):
        result = await repo.delete_metadata(9999)
        assert result is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_metadata_cascade(self, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-001", title="Test")
        assert meta.id is not None

        # 创建关联的 MediaFile
        media = await repo.create_media_file(library_id=1, path="/video/ABC-001.mp4", number="ABC-001")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        # 删除 Metadata 应级联清除 MediaFile 的关联
        await repo.delete_metadata(meta.id)

        updated_media = await repo.get_media_file(media.id)
        assert updated_media is not None
        assert updated_media.metadata_id is None
        assert updated_media.status == MediaFileStatus.PENDING

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_media_by_metadata_id(self, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-001", title="Test")
        assert meta.id is not None

        await repo.create_media_file(library_id=1, path="/video/ABC-001-a.mp4", number="ABC-001")
        await repo.create_media_file(library_id=1, path="/video/ABC-001-b.mp4", number="ABC-001")

        # 关联 MediaFile 到 Metadata
        media_files = await repo.list_media_files()
        for mf in media_files:
            assert mf.id is not None
            await repo.update_media_file(mf.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        associated = await repo.get_media_by_metadata_id(meta.id)
        assert len(associated) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_media_by_metadata_id_empty(self, repo: Repository):
        result = await repo.get_media_by_metadata_id(9999)
        assert result == []


class TestTaskRepo:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_get(self, repo: Repository):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"media_file_id": 1}, priority=5)
        assert task.id is not None
        assert task.status == TaskStatus.QUEUED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_claim_next_task(self, repo: Repository):
        await repo.create_task(task_type=TaskType.SCRAPE, payload={}, priority=1)
        await repo.create_task(task_type=TaskType.SCRAPE, payload={}, priority=10)
        await repo.create_task(task_type=TaskType.REFRESH, payload={}, priority=5)

        # 应返回优先级最高的队列中任务
        claimed = await repo.claim_next_task()
        assert claimed is not None
        assert claimed.priority == 10
        assert claimed.status == TaskStatus.RUNNING

        # 下一个应该是优先级 5
        claimed2 = await repo.claim_next_task()
        assert claimed2 is not None
        assert claimed2.priority == 5

    @pytest.mark.asyncio(loop_scope="function")
    async def test_claim_returns_none_when_empty(self, repo: Repository):
        assert await repo.claim_next_task() is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_complete_task(self, repo: Repository):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert task.id is not None
        await repo.claim_next_task()  # 标记为 RUNNING
        await repo.complete_task(task.id, result={"status": "ok"})

        fetched = await repo.get_task(task.id)
        assert fetched is not None
        assert fetched.status == TaskStatus.DONE
        assert fetched.result == {"status": "ok"}
        assert fetched.finished_at is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fail_task(self, repo: Repository):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert task.id is not None
        await repo.claim_next_task()
        await repo.fail_task(task.id, error="Connection timeout")

        fetched = await repo.get_task(task.id)
        assert fetched is not None
        assert fetched.status == TaskStatus.FAILED
        assert fetched.error == "Connection timeout"
        assert fetched.retries == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_task(self, repo: Repository):
        task = await repo.create_task(task_type=TaskType.REFRESH, payload={})
        assert task.id is not None

        result = await repo.delete_task(task.id)
        assert result is True
        assert await repo.get_task(task.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_task_not_found(self, repo: Repository):
        result = await repo.delete_task(9999)
        assert result is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_task_log_file(self, repo: Repository):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert task.id is not None

        await repo.update_task_log_file(task.id, "/logs/task-1.log")

        fetched = await repo.get_task(task.id)
        assert fetched is not None
        assert fetched.log_file == "/logs/task-1.log"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_tasks_with_filters(self, repo: Repository):
        await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        await repo.create_task(task_type=TaskType.REFRESH, payload={})

        scrape = await repo.list_tasks(task_types=[TaskType.SCRAPE])
        assert len(scrape) == 1
        scan = await repo.list_tasks(task_types=[TaskType.REFRESH])
        assert len(scan) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_count_tasks(self, repo: Repository):
        await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        await repo.create_task(task_type=TaskType.REFRESH, payload={})

        assert await repo.count_tasks() == 3
        assert await repo.count_tasks(task_types=[TaskType.SCRAPE]) == 2
        assert await repo.count_tasks(task_types=[TaskType.REFRESH]) == 1
        # 全部新建任务为 QUEUED, 无 DONE
        assert await repo.count_tasks(statuses=[TaskStatus.DONE]) == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_tasks_pagination(self, repo: Repository):
        for i in range(5):
            await repo.create_task(task_type=TaskType.SCRAPE, payload={"i": i}, priority=i)

        page1 = await repo.list_tasks(limit=2, offset=0, sort_by=TaskSortField.PRIORITY, order=SortOrder.ASC)
        page2 = await repo.list_tasks(limit=2, offset=2, sort_by=TaskSortField.PRIORITY, order=SortOrder.ASC)
        assert [t.priority for t in page1] == [0, 1]
        assert [t.priority for t in page2] == [2, 3]

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("order", "expected"),
        [
            (SortOrder.ASC, [1, 5, 10]),
            (SortOrder.DESC, [10, 5, 1]),
        ],
    )
    async def test_list_tasks_sort_by_priority(self, repo: Repository, order: SortOrder, expected: list[int]):
        for p in (5, 1, 10):
            await repo.create_task(task_type=TaskType.SCRAPE, payload={}, priority=p)

        items = await repo.list_tasks(sort_by=TaskSortField.PRIORITY, order=order)
        assert [t.priority for t in items] == expected

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("task_type", "first", "second", "same"),
        [
            (TaskType.ORGANIZE, {"library_id": 1}, {"library_id": 1}, True),
            (TaskType.ORGANIZE, {"library_id": 1}, {"library_id": 1, "write_nfo": False}, True),
            (TaskType.ORGANIZE, {"library_id": 1}, {"library_id": 2}, False),
            (TaskType.ACTOR_SCRAPE, {"actor_id": 3}, {"actor_id": 3}, True),
            (TaskType.ACTOR_SCRAPE, {"actor_id": 3}, {"actor_id": 3, "use_cache": []}, True),
            (TaskType.ACTOR_SCRAPE, {"actor_id": 3}, {"actor_id": 4}, False),
            (TaskType.SCRAPE, {"number": "A"}, {"number": "A"}, False),
            (TaskType.ORGANIZE, {}, {"library_id": 1}, False),
            (TaskType.ORGANIZE, {"library_id": "1"}, {"library_id": "1"}, False),
        ],
    )
    async def test_create_task_reuses_active_exclusive(
        self,
        repo: Repository,
        task_type: TaskType,
        first: dict[str, object],
        second: dict[str, object],
        same: bool,
    ):
        a = await repo.create_task(task_type=task_type, payload=first)
        b = await repo.create_task(task_type=task_type, payload=second)
        assert a.id is not None and b.id is not None
        assert (a.id == b.id) is same
        expected_count = 1 if same else 2
        assert await repo.count_tasks(task_types=[task_type]) == expected_count

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize("status", [TaskStatus.DONE, TaskStatus.FAILED])
    async def test_create_task_allows_new_after_terminal(self, repo: Repository, status: TaskStatus):
        first = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        assert first.id is not None
        if status == TaskStatus.DONE:
            await repo.complete_task(first.id)
        else:
            await repo.fail_task(first.id, "boom")
        second = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        assert second.id is not None
        assert second.id != first.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_task_reuses_running(self, repo: Repository):
        first = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == first.id
        second = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        assert second.id == first.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_task_concurrent_same_library(self, repo: Repository):
        first, second = await asyncio.gather(
            repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 7}),
            repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 7}),
        )
        assert first.id is not None and second.id is not None
        assert first.id == second.id
        assert await repo.count_tasks(task_types=[TaskType.ORGANIZE]) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_complete_followups_concurrent_exclusive(self, repo: Repository):
        """完成事务的入队互斥: 并发完成两个父任务、各自派生同 actor 的 ACTOR_SCRAPE, 只应建一行."""
        a = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "A"})
        b = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "B"})
        assert a.id is not None and b.id is not None
        await repo.claim_next_task()
        await repo.claim_next_task()

        results = await asyncio.gather(
            repo.complete_task_with_followups(
                a.id, result={}, followups=[("actor-scrape", TaskType.ACTOR_SCRAPE, {"actor_id": 5}, -1)]
            ),
            repo.complete_task_with_followups(
                b.id, result={}, followups=[("actor-scrape", TaskType.ACTOR_SCRAPE, {"actor_id": 5}, -1)]
            ),
        )
        # 两个完成事务各自返回同一个被复用的子任务行 (无重复提交).
        actor_ids = [t.id for batch in results for t in batch if t.id is not None]
        assert len(actor_ids) == 2
        assert actor_ids[0] == actor_ids[1]
        assert await repo.count_tasks(task_types=[TaskType.ACTOR_SCRAPE]) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_tasks_batch_reuses_within_session(self, repo: Repository):
        rows = await repo.create_tasks(TaskType.ORGANIZE, [{"library_id": 1}, {"library_id": 1}, {"library_id": 2}])
        assert rows[0].id == rows[1].id
        assert rows[2].id != rows[0].id
        assert await repo.count_tasks(task_types=[TaskType.ORGANIZE]) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retry_reuses_active_organize(self, repo: Repository):
        failed = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        assert failed.id is not None
        await repo.fail_task(failed.id, "boom")
        active = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        retried = await repo.retry_tasks([failed])
        assert [t.id for t in retried] == [active.id]


class TestListSorting:
    """跨资源的服务端排序: 字段 + 升降序 + 分页稳定性 (并列值用 id 作次级键)."""

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("sort_by", "order", "expected_numbers"),
        [
            (MetadataSortField.NUMBER, SortOrder.ASC, ["AAA-001", "BBB-002", "CCC-003"]),
            (MetadataSortField.NUMBER, SortOrder.DESC, ["CCC-003", "BBB-002", "AAA-001"]),
            (MetadataSortField.TITLE, SortOrder.ASC, ["BBB-002", "CCC-003", "AAA-001"]),
        ],
    )
    async def test_metadata_sort(
        self, repo: Repository, sort_by: MetadataSortField, order: SortOrder, expected_numbers: list[str]
    ):
        await repo.upsert_metadata(number="AAA-001", title="Zebra")
        await repo.upsert_metadata(number="BBB-002", title="Apple")
        await repo.upsert_metadata(number="CCC-003", title="Mango")

        items, _ = await repo.list_metadata(sort_by=sort_by, order=order)
        assert [m.number for m in items] == expected_numbers

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("sort_by", "order", "expected_numbers"),
        [
            (MediaSortField.NUMBER, SortOrder.ASC, ["A-1", "B-2", "C-3"]),
            (MediaSortField.NUMBER, SortOrder.DESC, ["C-3", "B-2", "A-1"]),
            (MediaSortField.PATH, SortOrder.ASC, ["A-1", "B-2", "C-3"]),
        ],
    )
    async def test_media_sort(
        self, repo: Repository, sort_by: MediaSortField, order: SortOrder, expected_numbers: list[str]
    ):
        await repo.create_media_file(library_id=1, path="/m/a.mp4", number="A-1")
        await repo.create_media_file(library_id=1, path="/m/b.mp4", number="B-2")
        await repo.create_media_file(library_id=1, path="/m/c.mp4", number="C-3")

        items = await repo.list_media_files(sort_by=sort_by, order=order)
        assert [m.number for m in items] == expected_numbers

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pagination_stable_on_ties(self, repo: Repository):
        """主排序键全部并列时, 次级 id 键保证分页不重不漏."""
        for i in range(6):
            await repo.create_media_file(library_id=1, path=f"/m/{i}.mp4", number="SAME")

        page1 = await repo.list_media_files(sort_by=MediaSortField.NUMBER, order=SortOrder.ASC, limit=3, offset=0)
        page2 = await repo.list_media_files(sort_by=MediaSortField.NUMBER, order=SortOrder.ASC, limit=3, offset=3)
        ids = [m.id for m in page1] + [m.id for m in page2]
        assert len(set(ids)) == 6  # 无重复, 无遗漏


class TestLibraryRepo:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_list(self, repo: Repository):
        await repo.create_library(name="incoming", path="/media/incoming", patterns=["*.mp4"])
        await repo.create_library(name="other", path="/media/other", automation=LibraryAutomation.NONE)

        all_paths = await repo.list_libraries()
        assert len(all_paths) == 2

        watched = await repo.list_libraries(watch_only=True)
        assert len(watched) == 1
        assert watched[0].path == "/media/incoming"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_library(self, repo: Repository):
        lib = await repo.create_library(name="x", path="/media/x")
        assert lib.id is not None
        removed = await repo.delete_library(lib.id)
        assert removed == 0
        assert await repo.list_libraries() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_library_cascades_media(self, repo: Repository):
        """删除 Library 级联删除其下所有 MediaFile, 且不影响其它库的文件."""
        lib = await repo.create_library(name="x", path="/media/x")
        other = await repo.create_library(name="y", path="/media/y")
        assert lib.id is not None and other.id is not None

        await repo.create_media_file(library_id=lib.id, path="/media/x/a.mp4")
        await repo.create_media_file(library_id=lib.id, path="/media/x/b.mp4")
        keep = await repo.create_media_file(library_id=other.id, path="/media/y/c.mp4")
        assert keep.id is not None

        removed = await repo.delete_library(lib.id)
        assert removed == 2

        # 该库下的文件全部删除, 其它库文件保留
        assert await repo.count_media_files(library_id=lib.id) == 0
        assert await repo.get_media_file(keep.id) is not None
        assert await repo.get_library(lib.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_library_not_found(self, repo: Repository):
        assert await repo.delete_library(9999) == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_library(self, repo: Repository):
        lib = await repo.create_library(name="test", path="/media/test")
        assert lib.id is not None

        fetched = await repo.get_library(lib.id)
        assert fetched is not None
        assert fetched.path == "/media/test"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_library_not_found(self, repo: Repository):
        assert await repo.get_library(9999) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_library_for_path(self, repo: Repository):
        await repo.create_library(name="root", path="/media/jav")
        sub = await repo.create_library(name="sub", path="/media/jav/sub")

        # 最长前缀匹配
        matched = await repo.get_library_for_path("/media/jav/sub/a.mp4")
        assert matched is not None and matched.id == sub.id
        # 无匹配
        assert await repo.get_library_for_path("/orphan/b.mp4") is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_library(self, repo: Repository):
        lib = await repo.create_library(name="old", path="/media/old", automation=LibraryAutomation.SCRAPE)
        assert lib.id is not None

        updated = await repo.update_library(lib.id, path="/media/new", automation=LibraryAutomation.NONE)
        assert updated is not None
        assert updated.path == "/media/new"
        assert updated.automation == LibraryAutomation.NONE

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_library_not_found(self, repo: Repository):
        result = await repo.update_library(9999, automation=LibraryAutomation.NONE)
        assert result is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_path_template_roundtrip(self, repo: Repository):
        """video_template 结构校验: 未闭合可选组在 repo 层拦截."""
        lib = await repo.create_library(name="t", path="/media/t")
        assert lib is not None and lib.id is not None
        assert lib.video_template == VIDEO_TEMPLATE_DEFAULT

        updated = await repo.update_library(lib.id, video_template="{number}/{number}[-CD{cd?}].{ext}")
        assert updated is not None
        assert updated.video_template == "{number}/{number}[-CD{cd?}].{ext}"

        with pytest.raises(ValueError, match="unclosed"):
            await repo.update_library(lib.id, video_template="{number}[-CD{cd?}.{ext}")
        with pytest.raises(ValueError, match="unknown mapping key"):
            await repo.update_library(lib.id, video_template="{mosaic?|uncencored=U}.{ext}")
        with pytest.raises(ValueError, match="unmatched"):
            await repo.create_library(name="bad", path="/media/bad", video_template="{number}].{ext}")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_subtitle_extensions_roundtrip(self, repo: Repository):
        lib = await repo.create_library(name="t", path="/media/t")
        assert lib is not None and lib.id is not None
        assert lib.subtitle_extensions == [".srt", ".ass", ".ssa", ".vtt", ".sub"]

        updated = await repo.update_library(lib.id, subtitle_extensions=["SRT", ".ass"])
        assert updated is not None
        assert updated.subtitle_extensions == [".srt", ".ass"]

        cleared = await repo.update_library(lib.id, subtitle_extensions=[])
        assert cleared is not None
        assert cleared.subtitle_extensions == []

        with pytest.raises(ValueError):
            await repo.update_library(lib.id, subtitle_extensions=[".srt/x"])

    @pytest.mark.asyncio(loop_scope="function")
    async def test_min_file_size_roundtrip(self, repo: Repository):
        lib = await repo.create_library(name="t", path="/media/t", min_file_size=50 * 1024 * 1024)
        assert lib.min_file_size == 50 * 1024 * 1024
        assert lib.id is not None
        updated = await repo.update_library(lib.id, min_file_size=0)
        assert updated is not None
        assert updated.min_file_size == 0
        with pytest.raises(ValueError, match="min_file_size"):
            await repo.update_library(lib.id, min_file_size=-1)


class TestScheduleRepo:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_get(self, repo: Repository):
        sched = await repo.create_schedule(
            cron="0 */6 * * *",
            task_type=RoutineType.CLEANUP,
            name="periodic scan",
            payload={"path": "/media"},
            enabled=True,
        )
        assert sched.id is not None
        assert sched.cron == "0 */6 * * *"
        assert sched.task_type == RoutineType.CLEANUP
        assert sched.name == "periodic scan"

        fetched = await repo.get_schedule(sched.id)
        assert fetched is not None
        assert fetched.payload == {"path": "/media"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_not_found(self, repo: Repository):
        assert await repo.get_schedule(9999) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_empty(self, repo: Repository):
        result = await repo.list_schedules()
        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_with_items(self, repo: Repository):
        await repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={})
        await repo.create_schedule(cron="0 12 * * *", task_type=RoutineType.CLEANUP, payload={})

        result = await repo.list_schedules()
        assert len(result) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_schedule(self, repo: Repository):
        sched = await repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={}, name="old")
        assert sched.id is not None

        updated = await repo.update_schedule(sched.id, name="new", enabled=False)
        assert updated is not None
        assert updated.name == "new"
        assert updated.enabled is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_not_found(self, repo: Repository):
        result = await repo.update_schedule(9999, enabled=False)
        assert result is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete(self, repo: Repository):
        sched = await repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={})
        assert sched.id is not None

        result = await repo.delete_schedule(sched.id)
        assert result is True
        assert await repo.list_schedules() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_not_found(self, repo: Repository):
        result = await repo.delete_schedule(9999)
        assert result is False


class TestRepositoryInit:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_initialize_runs_without_error(self, repo: Repository):
        """initialize() 在当前已创建表的内存 DB 中应无错误执行"""
        await repo.initialize()
