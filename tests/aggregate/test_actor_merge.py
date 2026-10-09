"""演员聚合 merge 与查找名构造单元测试."""

from __future__ import annotations

import pytest

from amane.aggregate.actor import (
    AggregatedActor,
    merge_actor_metadata,
    merge_actor_rows_fill_empty,
    merge_actor_scrape_result,
)
from amane.crawlers.actor import ActorMetadata
from amane.db.actor_person import (
    actor_to_aggregated,
    apply_aggregated_to_actor,
    filter_locked_person_data,
    merge_person_fields_into_target,
)
from amane.db.models import Actor
from amane.enums import ActorField, ActorGender, SiteName


class TestMergeActorMetadata:
    def test_scalar_fill_empty_by_profile_order(self):
        results = {
            SiteName.MINNANO: ActorMetadata(birthday="1990-01-02", height=160),
            SiteName.WIKIPEDIA: ActorMetadata(birthday="1990-01-01", overview="bio", tagline="idol"),
        }
        out = merge_actor_metadata(
            results, profile_sites=[SiteName.MINNANO, SiteName.WIKIPEDIA], image_sites=[SiteName.GFRIENDS]
        )
        assert out.birthday == "1990-01-02"
        assert out.field_sources["birthday"] == "minnano"
        assert out.height == 160
        assert out.overview == "bio"
        assert out.field_sources["overview"] == "wikipedia"
        assert out.tagline == "idol"

    def test_image_sites_before_profile_images(self):
        results = {
            SiteName.MINNANO: ActorMetadata(image_urls=["http://minnano/a.jpg"]),
            SiteName.GFRIENDS: ActorMetadata(image_urls=["http://gfriends/b.jpg"]),
        }
        out = merge_actor_metadata(results, profile_sites=[SiteName.MINNANO], image_sites=[SiteName.GFRIENDS])
        assert out.image_urls == ["http://gfriends/b.jpg", "http://minnano/a.jpg"]
        assert out.field_sources["image_urls"] == "gfriends"

    def test_aliases_and_provider_ids_union(self):
        results = {
            SiteName.MINNANO: ActorMetadata(aliases=["A", "B"], provider_ids={"minnano": "1"}),
            SiteName.WIKIPEDIA: ActorMetadata(aliases=["B", "C"], provider_ids={"wikidata": "Q1"}),
        }
        out = merge_actor_metadata(results, profile_sites=[SiteName.MINNANO, SiteName.WIKIPEDIA], image_sites=[])
        assert out.aliases == ["A", "B", "C"]
        assert out.provider_ids == {"minnano": "1", "wikidata": "Q1"}

    def test_site_display_name_joins_alias_bag(self):
        """站点主显示名不当身份, 与 aliases 一并入袋."""
        results = {SiteName.JAVDB: ActorMetadata(name="筧純", aliases=["鷲尾芽衣", "筧ジュン", "鷲尾めい"])}
        out = merge_actor_metadata(results, profile_sites=[SiteName.JAVDB], image_sites=[])
        assert out.aliases == ["筧純", "鷲尾芽衣", "筧ジュン", "鷲尾めい"]

    def test_source_urls_collected_from_all_sites(self):
        results = {
            SiteName.MINNANO: ActorMetadata(source_url="https://minnano/a"),
            SiteName.WIKIPEDIA: ActorMetadata(source_url="https://wikipedia/b"),
            SiteName.GFRIENDS: ActorMetadata(image_urls=["http://g/1.jpg"]),
        }
        out = merge_actor_metadata(
            results, profile_sites=[SiteName.MINNANO, SiteName.WIKIPEDIA], image_sites=[SiteName.GFRIENDS]
        )
        assert out.source_urls == {
            "minnano": "https://minnano/a",
            "wikipedia": "https://wikipedia/b",
        }

    def test_none_sites_skipped(self):
        out = merge_actor_metadata(
            {SiteName.MINNANO: None, SiteName.WIKIPEDIA: ActorMetadata(overview="x")},
            profile_sites=[SiteName.MINNANO, SiteName.WIKIPEDIA],
            image_sites=[],
        )
        assert out.overview == "x"
        assert "minnano" not in out.raw
        assert "wikipedia" in out.raw

    def test_gender_unknown_filled_from_source(self):
        results = {
            SiteName.WIKIPEDIA: ActorMetadata(gender=ActorGender.FEMALE, overview="bio"),
        }
        out = merge_actor_metadata(results, profile_sites=[SiteName.WIKIPEDIA], image_sites=[])
        assert out.gender == ActorGender.FEMALE
        assert out.field_sources["gender"] == "wikipedia"

    def test_gender_male_not_overwritten_by_female(self):
        target = AggregatedActor(gender=ActorGender.MALE, overview="keep")
        source = AggregatedActor(gender=ActorGender.FEMALE, overview="new", field_sources={"gender": "minnano"})
        merged = merge_actor_rows_fill_empty(target, source)
        assert merged.gender == ActorGender.MALE
        assert merged.overview == "keep"


class TestMergeActorRows:
    def test_fill_empty_preserves_target(self):
        target = AggregatedActor(birthday="1990-01-01", aliases=["T"], overview=None)
        source = AggregatedActor(birthday="2000-01-01", aliases=["S"], overview="bio", height=155)
        out = merge_actor_rows_fill_empty(target, source)
        assert out.birthday == "1990-01-01"
        assert out.overview == "bio"
        assert out.height == 155
        assert out.aliases == ["T", "S"]


class TestActorPersonHelpers:
    def test_merge_person_fields_into_target(self):
        target = Actor(name="Canonical", birthday=None, overview="keep")
        source = Actor(
            name="AliasEN",
            birthday="1991-02-03",
            overview="drop",
            image_urls=["http://x/1.jpg"],
            provider_ids={"wikidata": "Q9"},
            raw={"wikipedia": {"overview": "drop"}},
        )
        merge_person_fields_into_target(target, [source])
        assert target.birthday == "1991-02-03"
        assert target.overview == "keep"
        assert target.image_urls == ["http://x/1.jpg"]
        assert target.provider_ids == {"wikidata": "Q9"}
        assert "wikipedia" in target.raw

    def test_apply_aggregated_keeps_name(self):
        """写回不修改 name/id."""
        other = Actor(name="B")
        apply_aggregated_to_actor(other, actor_to_aggregated(Actor(name="A", height=160)))
        assert other.name == "B"

    def test_merge_keeps_site_aliases_in_memory(self):
        """站点名并入聚合别名 (落库行化由 repo 层负责)."""
        actor = Actor(name="鷲尾めい")
        site = AggregatedActor(aliases=["筧純", "鷲尾芽衣", "筧ジュン", "鷲尾めい"])
        merged = merge_actor_rows_fill_empty(actor_to_aggregated(actor), site)
        assert actor.name == "鷲尾めい"
        assert merged.aliases == ["筧純", "鷲尾芽衣", "筧ジュン", "鷲尾めい"]

    def test_filter_locked_person_data_keeps_aliases(self):
        """别名无库内值可回填, 锁定它不得清空 (回填由别名行写入点负责)."""
        data = AggregatedActor(aliases=["站点别名"], overview="new")
        current = AggregatedActor(overview="old")
        out = filter_locked_person_data(data, locked={ActorField.ALIASES, ActorField.OVERVIEW}, current=current)
        assert out.aliases == ["站点别名"]
        assert out.overview == "old"


class TestMergeActorScrapeResult:
    @pytest.mark.parametrize(
        ("current", "fetched", "expected"),
        [
            # 本次非空压过库内; 本次为空的位置取库内值.
            (
                AggregatedActor(height=155, field_sources={"birthday": "minnano", "height": "gfriends"}),
                AggregatedActor(birthday="2000-01-01", overview="bio", field_sources={"birthday": "javdb"}),
                {"birthday": "2000-01-01", "overview": "bio", "height": 155},
            ),
            # 本次有图即替换库内列表.
            (
                AggregatedActor(image_urls=["old.jpg"]),
                AggregatedActor(image_urls=["new.jpg"]),
                {"image_urls": ["new.jpg"]},
            ),
            # 本次无图才保留库内列表.
            (AggregatedActor(image_urls=["old.jpg"]), AggregatedActor(), {"image_urls": ["old.jpg"]}),
            # 别名并集; 本次站点名在前.
            (AggregatedActor(aliases=["既有"]), AggregatedActor(aliases=["站点"]), {"aliases": ["站点", "既有"]}),
        ],
    )
    def test_fetched_wins_with_fill_empty_fallback(
        self, current: AggregatedActor, fetched: AggregatedActor, expected: dict[str, object]
    ):
        out = merge_actor_scrape_result(fetched, current)
        assert {key: getattr(out, key) for key in expected} == expected

    def test_field_sources_follow_the_winning_value(self):
        current = AggregatedActor(height=155, field_sources={"birthday": "minnano", "height": "gfriends"})
        fetched = AggregatedActor(birthday="2000-01-01", field_sources={"birthday": "javdb"})
        out = merge_actor_scrape_result(fetched, current)
        assert out.field_sources == {"birthday": "javdb", "height": "gfriends"}

    def test_site_snapshot_replaced_wholesale_and_absent_sites_kept(self):
        """本次快照整段覆盖 (旧键随之消失), 未参与站点的快照保留."""
        current = AggregatedActor(
            raw={"minnano": {"overview": "stale", "birthday": "1980-01-01"}, "gfriends": {"image_urls": ["old"]}}
        )
        fetched = AggregatedActor(raw={"minnano": {"overview": "fresh"}})
        out = merge_actor_scrape_result(fetched, current)
        assert out.raw == {"minnano": {"overview": "fresh"}, "gfriends": {"image_urls": ["old"]}}
        assert "birthday" not in out.raw["minnano"]

    def test_fetched_not_mutated(self):
        fetched = AggregatedActor(overview="x")
        merge_actor_scrape_result(fetched, AggregatedActor(height=155))
        assert fetched.height is None
