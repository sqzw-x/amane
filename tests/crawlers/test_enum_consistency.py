"""StrEnum 一致性测试 - 确保枚举成员与运行时注册表保持同步."""

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

    def test_javdb_is_dual_role(self):
        assert SiteName.JAVDB in FILM_METADATA_SITES
        assert SiteName.JAVDB in ACTOR_PROFILE_SITES
        assert SiteName.JAVDB not in ACTOR_ONLY_SITES

    def test_theporndb_is_dual_role(self):
        assert SiteName.THEPORNDB in FILM_METADATA_SITES
        assert SiteName.THEPORNDB in ACTOR_PROFILE_SITES
        assert SiteName.THEPORNDB not in ACTOR_ONLY_SITES

    def test_actor_sites_match_actor_registry(self):
        registered = set(actor_registry.sites())
        expected = {s.value for s in (*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES)}
        assert expected <= registered

    def test_multi_language_sites_are_film_sites(self):
        assert frozenset(FILM_METADATA_SITES) >= MULTI_LANGUAGE_SITES
        assert SiteName.IQQTV in MULTI_LANGUAGE_SITES
        assert SiteName.R18DEV in MULTI_LANGUAGE_SITES
