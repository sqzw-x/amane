"""演员站点性别覆盖单元测试."""

from __future__ import annotations

import pytest

from amane.crawlers.actor import filter_sites_for_gender, site_allows_actor_gender
from amane.enums import ActorGender, SiteName


@pytest.mark.parametrize(
    ("gender", "site", "allowed"),
    [
        (ActorGender.FEMALE, SiteName.MINNANO, True),
        (ActorGender.FEMALE, SiteName.GFRIENDS, True),
        (ActorGender.FEMALE, SiteName.JAVDB, True),
        (ActorGender.FEMALE, SiteName.WIKIPEDIA, True),
        (ActorGender.FEMALE, SiteName.THEPORNDB, True),
        (ActorGender.MALE, SiteName.MINNANO, False),
        (ActorGender.MALE, SiteName.GFRIENDS, False),
        (ActorGender.MALE, SiteName.JAVDB, True),
        (ActorGender.MALE, SiteName.WIKIPEDIA, True),
        (ActorGender.MALE, SiteName.THEPORNDB, True),
        (ActorGender.UNKNOWN, SiteName.MINNANO, False),
        (ActorGender.UNKNOWN, SiteName.GFRIENDS, False),
        (ActorGender.UNKNOWN, SiteName.JAVDB, True),
        (ActorGender.UNKNOWN, SiteName.WIKIPEDIA, True),
        (ActorGender.UNKNOWN, SiteName.THEPORNDB, True),
    ],
)
def test_site_allows_actor_gender(gender: ActorGender, site: SiteName, allowed: bool) -> None:
    assert site_allows_actor_gender(site, gender) is allowed


def test_filter_sites_preserves_order() -> None:
    configured = [SiteName.MINNANO, SiteName.JAVDB, SiteName.WIKIPEDIA, SiteName.GFRIENDS, SiteName.THEPORNDB]
    allowed, skipped = filter_sites_for_gender(configured, ActorGender.MALE)
    assert allowed == [SiteName.JAVDB, SiteName.WIKIPEDIA, SiteName.THEPORNDB]
    assert skipped == [SiteName.MINNANO, SiteName.GFRIENDS]

    allowed_f, skipped_f = filter_sites_for_gender(configured, ActorGender.FEMALE)
    assert allowed_f == configured
    assert skipped_f == []

    allowed_u, skipped_u = filter_sites_for_gender(configured, ActorGender.UNKNOWN)
    assert allowed_u == [SiteName.JAVDB, SiteName.WIKIPEDIA, SiteName.THEPORNDB]
    assert skipped_u == [SiteName.MINNANO, SiteName.GFRIENDS]
