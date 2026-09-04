"""站点角色常量与配置 schema 收窄测试."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from amane.config.manager import ActorScrapingConfig, ScrapingConfig
from amane.crawlers import actor_registry, registry
from amane.crawlers.site_roles import ACTOR_IMAGE_SITES, ACTOR_PROFILE_SITES, FILM_METADATA_SITES
from amane.enums import MetadataField, SiteName
from amane.parsing import ContentType


class TestSiteCapabilitySchema:
    def test_actor_profile_schema_enum(self):
        schema = ActorScrapingConfig.model_json_schema()
        items = schema["properties"]["profile_sites"]["items"]
        assert items["enum"] == list(ACTOR_PROFILE_SITES)
        assert SiteName.DMM not in items["enum"]
        assert SiteName.GFRIENDS not in items["enum"]

    def test_actor_image_schema_enum(self):
        schema = ActorScrapingConfig.model_json_schema()
        items = schema["properties"]["image_sites"]["items"]
        assert items["enum"] == list(ACTOR_IMAGE_SITES)
        assert SiteName.MINNANO not in items["enum"]

    def test_film_content_routes_value_items_enum(self):
        schema = ScrapingConfig.model_json_schema()
        props = schema["properties"]["content_routes"]["additionalProperties"]
        assert set(props["items"]["enum"]) == set(FILM_METADATA_SITES)
        assert props.get("x-ordered") is True
        assert SiteName.MINNANO not in props["items"]["enum"]
        assert SiteName.WIKIPEDIA not in props["items"]["enum"]
        assert SiteName.GFRIENDS not in props["items"]["enum"]

    def test_film_field_priority_value_items_enum(self):
        schema = ScrapingConfig.model_json_schema()
        props = schema["properties"]["field_priority"]["additionalProperties"]
        assert props["items"]["enum"] == list(FILM_METADATA_SITES)
        assert props.get("x-ordered") is True
        assert schema["properties"]["field_priority"].get("x-frozen-keys") is not True

    def test_film_field_blacklist_value_items_enum(self):
        schema = ScrapingConfig.model_json_schema()
        props = schema["properties"]["field_blacklist"]["additionalProperties"]
        assert props["items"]["enum"] == list(FILM_METADATA_SITES)
        assert props.get("x-ordered") is not True
        assert schema["properties"]["field_blacklist"].get("x-frozen-keys") is not True


class TestSiteCapabilityValidation:
    def test_actor_profile_rejects_film_only_site(self):
        with pytest.raises(ValidationError, match="profile_sites"):
            ActorScrapingConfig(profile_sites=[SiteName.DMM])

    def test_actor_profile_accepts_sites_also_in_film_registry(self):
        dual = [
            SiteName(name)
            for name in actor_registry.sites()
            if registry.get(name) is not None and SiteName(name) in ACTOR_PROFILE_SITES
        ]
        assert dual
        cfg = ActorScrapingConfig(profile_sites=dual)
        assert cfg.profile_sites == dual

    def test_actor_image_rejects_profile_site(self):
        with pytest.raises(ValidationError, match="image_sites"):
            ActorScrapingConfig(image_sites=[SiteName.MINNANO])

    def test_film_field_priority_rejects_actor_site(self):
        with pytest.raises(ValidationError, match="field_priority"):
            ScrapingConfig(field_priority={MetadataField.TITLE: [SiteName.MINNANO]})

    def test_film_field_blacklist_rejects_actor_site(self):
        with pytest.raises(ValidationError, match="field_blacklist"):
            ScrapingConfig(field_blacklist={MetadataField.TITLE: [SiteName.MINNANO]})

    def test_content_routes_rejects_actor_site(self):
        with pytest.raises(ValidationError, match="content_routes"):
            ScrapingConfig(content_routes={ContentType.CENSORED: [SiteName.GFRIENDS]})
