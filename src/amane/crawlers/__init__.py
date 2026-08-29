from ..enums import Language, MetadataField, SiteName
from .actor import (
    ActorCrawler,
    ActorMetadata,
    GFriendsActorCrawler,
    JavDBActorCrawler,
    MinnanoActorCrawler,
    ThePornDBActorCrawler,
    WikipediaActorCrawler,
    actor_registry,
)
from .base import Crawler
from .http import HttpClient
from .models import FetchOptions, MediaMetadata
from .registry import registry
from .sites import (
    AiravCrawler,
    AvsoxCrawler,
    DahliaCrawler,
    DmmCrawler,
    FalenoCrawler,
    FC2ClubCrawler,
    FC2Crawler,
    FC2PPVDBCrawler,
    FreejavbtCrawler,
    GetchuCrawler,
    GigaCrawler,
    IqqtvCrawler,
    Jav321Crawler,
    JavBusCrawler,
    JavDBCrawler,
    JavLibraryCrawler,
    Kin8Crawler,
    MGStageCrawler,
    OfficialCrawler,
    PrestigeCrawler,
    R18DevCrawler,
    ThePornDBCrawler,
    XCityCrawler,
)

# 注册所有影片爬虫
registry.register(JavDBCrawler)
registry.register(DmmCrawler)
registry.register(JavBusCrawler)
registry.register(MGStageCrawler)
registry.register(FC2Crawler)
registry.register(JavLibraryCrawler)
registry.register(FreejavbtCrawler)
registry.register(Jav321Crawler)
registry.register(AiravCrawler)
registry.register(AvsoxCrawler)
registry.register(XCityCrawler)
registry.register(DahliaCrawler)
registry.register(FalenoCrawler)
registry.register(GigaCrawler)
registry.register(Kin8Crawler)
registry.register(FC2ClubCrawler)
registry.register(FC2PPVDBCrawler)
registry.register(GetchuCrawler)
registry.register(IqqtvCrawler)
registry.register(PrestigeCrawler)
registry.register(R18DevCrawler)
registry.register(ThePornDBCrawler)
registry.register(OfficialCrawler)

# 演员站注册在 amane.crawlers.actor 导入时完成

__all__ = [
    "ActorCrawler",
    "ActorMetadata",
    "AiravCrawler",
    "AvsoxCrawler",
    "Crawler",
    "DahliaCrawler",
    "DmmCrawler",
    "FC2ClubCrawler",
    "FC2Crawler",
    "FC2PPVDBCrawler",
    "FalenoCrawler",
    "FetchOptions",
    "FreejavbtCrawler",
    "GFriendsActorCrawler",
    "GetchuCrawler",
    "GigaCrawler",
    "HttpClient",
    "IqqtvCrawler",
    "Jav321Crawler",
    "JavBusCrawler",
    "JavDBActorCrawler",
    "JavDBCrawler",
    "JavLibraryCrawler",
    "Kin8Crawler",
    "Language",
    "MGStageCrawler",
    "MediaMetadata",
    "MetadataField",
    "MinnanoActorCrawler",
    "OfficialCrawler",
    "PrestigeCrawler",
    "R18DevCrawler",
    "SiteName",
    "ThePornDBActorCrawler",
    "ThePornDBCrawler",
    "WikipediaActorCrawler",
    "XCityCrawler",
    "actor_registry",
    "registry",
]
