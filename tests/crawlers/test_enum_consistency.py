"""StrEnum / 注册表 / profile() 声明一致性 — 站点名单由注册表推导, 不测具名双料站."""

from amane.crawlers import MetadataField, SiteName, actor_registry, registry
from amane.crawlers.models import MediaMetadata
from amane.crawlers.site_roles import (
    ACTOR_IMAGE_SITES,
    ACTOR_ONLY_SITES,
    ACTOR_PROFILE_SITES,
    FILM_METADATA_SITES,
    MULTI_LANGUAGE_SITES,
)
from amane.plugins.models import SourceCapability, SourceTrait

_ACTOR_CAPS = frozenset({SourceCapability.ACTOR_PROFILE, SourceCapability.ACTOR_IMAGE})


class TestEnumConsistency:
    """验证枚举定义与运行时状态的一致性."""

    def test_site_name_matches_registry(self):
        """影片 registry 覆盖的 SiteName 须与注册表一致; ACTOR_ONLY_SITES 另计."""
        registered = set(registry.sites())
        enum_values = set(SiteName)
        film_enum = enum_values - set(ACTOR_ONLY_SITES)
        assert film_enum == registered, (
            f"Mismatch: enum_only={film_enum - registered}, registry_only={registered - film_enum}"
        )
        assert enum_values >= set(ACTOR_ONLY_SITES)
        assert set(FILM_METADATA_SITES) == film_enum

    def test_site_name_enum_is_sorted(self):
        """SiteName 成员应按字母序排列 (方便维护)."""
        values = list(SiteName)
        assert values == sorted(values), "SiteName members are not in alphabetical order"

    def test_metadata_field_matches_media_metadata(self):
        """MetadataField 标量+URL 字段必须存在于 MediaMetadata 中."""
        model_fields = set(MediaMetadata.model_fields.keys())
        for field in MetadataField:
            assert field in model_fields, f"MetadataField.{field.name} ('{field}') not in MediaMetadata"

    def test_actor_crawlers_declare_actor_capabilities(self):
        for name in actor_registry.sites():
            cls = actor_registry.get(name)
            assert cls is not None
            profile = cls.profile()
            caps = profile.capabilities
            assert caps & _ACTOR_CAPS, f"{name} must declare actor_profile or actor_image"
            assert SourceCapability.FILM_METADATA not in caps
            assert profile.genders, f"{name} must declare genders"

    def test_profile_and_image_partition_actor_registry(self):
        profile = frozenset(ACTOR_PROFILE_SITES)
        image = frozenset(ACTOR_IMAGE_SITES)
        assert not profile & image
        assert set(profile | image) == set(actor_registry.sites())

    def test_sites_in_both_registries_are_not_actor_only(self):
        both = set(registry.sites()) & set(actor_registry.sites())
        actor_lists = {*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES}
        assert both <= actor_lists
        assert both <= set(FILM_METADATA_SITES)
        assert both.isdisjoint(ACTOR_ONLY_SITES)

    def test_multi_language_follows_film_trait(self):
        from_traits = frozenset(
            SiteName(name)
            for name in registry.sites()
            if (cls := registry.get(name)) is not None and SourceTrait.MULTI_LANGUAGE in cls.profile().traits
        )
        assert from_traits == MULTI_LANGUAGE_SITES
        assert frozenset(FILM_METADATA_SITES) >= MULTI_LANGUAGE_SITES

    def test_file_hash_trait_is_stash_film_crawlers(self):
        """uses_file_hash 只出现在需要指纹匹配的影片站 (当前 ThePornDB)."""
        flagged = frozenset(
            SiteName(name)
            for name in registry.sites()
            if (cls := registry.get(name)) is not None and SourceTrait.USES_FILE_HASH in cls.profile().traits
        )
        assert flagged == frozenset({SiteName.THEPORNDB})
        assert flagged <= frozenset(FILM_METADATA_SITES)
