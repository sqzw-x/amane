"""演员站点性别覆盖 — 对照各爬虫 ``profile().genders``, 不维护平行名单."""

from __future__ import annotations

from amane.crawlers.actor import actor_registry, filter_sites_for_gender, site_allows_actor_gender
from amane.crawlers.site_roles import ACTOR_IMAGE_SITES, ACTOR_PROFILE_SITES
from amane.enums import ActorGender, SiteName


def _supported(site: SiteName) -> frozenset[ActorGender]:
    cls = actor_registry.get(site.value)
    if cls is None:
        return frozenset()
    return cls.profile().genders or frozenset()


def test_site_allows_actor_gender_follows_profile() -> None:
    for name in actor_registry.sites():
        site = SiteName(name)
        supported = _supported(site)
        assert site_allows_actor_gender(site, ActorGender.FEMALE) is (ActorGender.FEMALE in supported)
        assert site_allows_actor_gender(site, ActorGender.MALE) is (ActorGender.MALE in supported)
        both = ActorGender.FEMALE in supported and ActorGender.MALE in supported
        assert site_allows_actor_gender(site, ActorGender.UNKNOWN) is both
    assert site_allows_actor_gender(SiteName.DMM, ActorGender.FEMALE) is False


def test_filter_sites_preserves_order() -> None:
    configured = [*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES]
    for gender in ActorGender:
        allowed, skipped = filter_sites_for_gender(configured, gender)
        expected_allowed = [site for site in configured if site_allows_actor_gender(site, gender)]
        expected_skipped = [site for site in configured if not site_allows_actor_gender(site, gender)]
        assert allowed == expected_allowed
        assert skipped == expected_skipped
