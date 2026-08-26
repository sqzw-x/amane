"""演员聚合 merge 与查找名构造单元测试."""

from __future__ import annotations

from amane.aggregate.actor import AggregatedActor, merge_actor_metadata, merge_actor_rows_fill_empty
from amane.crawlers.actor import ActorMetadata
from amane.db.actor_person import actor_to_aggregated, apply_aggregated_to_actor, merge_person_fields_into_target
from amane.db.models import Actor
from amane.enums import ActorGender, SiteName


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

    def test_roundtrip_aggregated(self):
        actor = Actor(name="A", height=160, image_urls=["u"])
        data = actor_to_aggregated(actor)
        other = Actor(name="B")
        apply_aggregated_to_actor(other, data)
        assert other.height == 160
        assert other.image_urls == ["u"]
        assert other.name == "B"

    def test_merge_keeps_site_aliases_in_memory(self):
        """站点名并入聚合别名 (落库行化由 repo 层负责)."""
        actor = Actor(name="鷲尾めい")
        site = AggregatedActor(aliases=["筧純", "鷲尾芽衣", "筧ジュン", "鷲尾めい"])
        merged = merge_actor_rows_fill_empty(actor_to_aggregated(actor), site)
        assert actor.name == "鷲尾めい"
        assert merged.aliases == ["筧純", "鷲尾芽衣", "筧ジュン", "鷲尾めい"]
