"""演员档案站性别覆盖 — 读各爬虫 ``profile().genders``, 不另维护名单."""

from __future__ import annotations

from amane.enums import ActorGender, SiteName

from .registry import actor_registry


def site_allows_actor_gender(site: SiteName, gender: ActorGender) -> bool:
    """当前演员性别是否允许请求该站.

    - female / male: 站点 ``profile().genders`` 含该性别
    - unknown (保守): 仅双向站 (同时覆盖 female 与 male), 避免误撞女-only 站
    """
    cls = actor_registry.get(site)
    if cls is None:
        return False
    supported = cls.profile().genders
    if not supported:
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
