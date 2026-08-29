"""演员档案站性别覆盖 - 代码常量, 不进 HotSettings."""

from __future__ import annotations

from amane.enums import ActorGender, SiteName

# 各站能可靠提供的性别. 女-only 站在 male/unknown 时不得请求.
ACTOR_SITE_GENDERS: dict[SiteName, frozenset[ActorGender]] = {
    SiteName.MINNANO: frozenset({ActorGender.FEMALE}),
    SiteName.GFRIENDS: frozenset({ActorGender.FEMALE}),
    SiteName.JAVDB: frozenset({ActorGender.FEMALE, ActorGender.MALE}),
    SiteName.WIKIPEDIA: frozenset({ActorGender.FEMALE, ActorGender.MALE}),
    SiteName.THEPORNDB: frozenset({ActorGender.FEMALE, ActorGender.MALE}),
}


def site_allows_actor_gender(site: SiteName, gender: ActorGender) -> bool:
    """当前演员性别是否允许请求该站.

    - female / male: 站点覆盖含该性别
    - unknown (保守): 仅双向站 (同时覆盖 female 与 male), 避免误撞女-only 站
    """
    supported = ACTOR_SITE_GENDERS.get(site)
    if supported is None:
        return False
    if gender == ActorGender.UNKNOWN:
        return ActorGender.FEMALE in supported and ActorGender.MALE in supported
    return gender in supported


def filter_sites_for_gender(sites: list[SiteName], gender: ActorGender) -> tuple[list[SiteName], list[SiteName]]:
    """返回 (允许的站点保序, 因性别跳过的站点)."""
    allowed: list[SiteName] = []
    skipped: list[SiteName] = []
    for site in sites:
        if site_allows_actor_gender(site, gender):
            allowed.append(site)
        else:
            skipped.append(site)
    return allowed, skipped
