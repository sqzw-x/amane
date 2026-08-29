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
from amane.enums import SiteName as SiteNameEnum
from amane.plugins.models import SourceCapability

_ACTOR_CAPS = frozenset({SourceCapability.ACTOR_PROFILE, SourceCapability.ACTOR_IMAGE})


class TestEnumConsistency:
    """验证枚举定义与运行时状态的一致性."""

    def test_site_name_matches_registry(self):
        """影片 registry 覆盖的 SiteName 须与注册表一致; ACTOR_ONLY_SITES 另计."""
        registered = set(registry.sites())
        enum_values = {s.value for s in SiteName}
        film_enum = enum_values - {s.value for s in ACTOR_ONLY_SITES}
        assert film_enum == registered, (
            f"Mismatch: enum_only={film_enum - registered}, registry_only={registered - film_enum}"
        )
        assert enum_values >= {s.value for s in ACTOR_ONLY_SITES}
        assert {s.value for s in FILM_METADATA_SITES} == film_enum

    def test_site_name_enum_is_sorted(self):
        """SiteName 成员应按字母序排列 (方便维护)."""
        values = [s.value for s in SiteName]
        assert values == sorted(values), "SiteName members are not in alphabetical order"

    def test_metadata_field_matches_media_metadata(self):
        """MetadataField 标量+URL 字段必须存在于 MediaMetadata 中."""
        model_fields = set(MediaMetadata.model_fields.keys())
        for field in MetadataField:
            assert field.value in model_fields, f"MetadataField.{field.name} ('{field.value}') not in MediaMetadata"

    def test_actor_only_sites_are_site_name_members(self):
        for site in ACTOR_ONLY_SITES:
            assert isinstance(site, SiteNameEnum)

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
        assert {s.value for s in profile | image} == set(actor_registry.sites())

    def test_sites_in_both_registries_are_not_actor_only(self):
        both = set(registry.sites()) & set(actor_registry.sites())
        actor_lists = {s.value for s in (*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES)}
        assert both <= actor_lists
        assert both <= {s.value for s in FILM_METADATA_SITES}
        assert both.isdisjoint({s.value for s in ACTOR_ONLY_SITES})

    def test_multi_language_follows_film_profile_flag(self):
        from_profile = frozenset(
            SiteName(name)
            for name in registry.sites()
            if (cls := registry.get(name)) is not None and cls.profile().multi_language
        )
        assert from_profile == MULTI_LANGUAGE_SITES
        assert frozenset(FILM_METADATA_SITES) >= MULTI_LANGUAGE_SITES
