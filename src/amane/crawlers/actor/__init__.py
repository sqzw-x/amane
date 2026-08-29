"""演员爬虫子包 - 档案/头像源与影片爬虫隔离.

性别覆盖见 site_coverage; 配置侧站点角色见 ..site_roles.
"""

from .base import ActorCrawler, ActorFetcher
from .models import ActorMetadata
from .registry import actor_registry
from .site_coverage import ACTOR_SITE_GENDERS, filter_sites_for_gender, site_allows_actor_gender
from .sites import (
    GFriendsActorCrawler,
    JavDBActorCrawler,
    MinnanoActorCrawler,
    ThePornDBActorCrawler,
    WikipediaActorCrawler,
)

actor_registry.register(MinnanoActorCrawler)
actor_registry.register(JavDBActorCrawler)
actor_registry.register(WikipediaActorCrawler)
actor_registry.register(GFriendsActorCrawler)
actor_registry.register(ThePornDBActorCrawler)

__all__ = [
    "ACTOR_SITE_GENDERS",
    "ActorCrawler",
    "ActorFetcher",
    "ActorMetadata",
    "GFriendsActorCrawler",
    "JavDBActorCrawler",
    "MinnanoActorCrawler",
    "ThePornDBActorCrawler",
    "WikipediaActorCrawler",
    "actor_registry",
    "filter_sites_for_gender",
    "site_allows_actor_gender",
]
