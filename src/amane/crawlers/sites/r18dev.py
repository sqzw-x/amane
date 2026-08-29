"""R18Dev 爬虫 - 查询离线 PostgreSQL 镜像而非 HTTP 抓取.

特殊爬虫: 不走 _search/_scrape 两步 HTTP 模板, 直接 override fetch() 用 SQL 查询. 持有一个
只读 R18Database (asyncpg 连接池, 并发安全), 每次 fetch() 开短生命周期 session.

优雅降级: PG 未配置 (db is None) 或镜像未导入/查询失败时返回 None, 不中断多源聚合.
"""

import time
from typing import TYPE_CHECKING, override

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from ..r18dev import R18Repository, content_id_candidates, to_metadata

if TYPE_CHECKING:
    from ...config import SiteConfig
    from ..http import HttpClient
    from ..r18dev import R18Database


class R18DevCrawler(Crawler):
    """r18.dev 离线镜像爬虫. db 由 CrawlerFactory 在构造期注入."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        # base_url 仅占位 (本爬虫不发 HTTP); 保留以满足注册表/限速器构建约定.
        return CrawlerProfile(name=SiteName.R18DEV, base_url="https://r18.dev", multi_language=True)

    def __init__(self, client: HttpClient, config: SiteConfig | None = None, db: R18Database | None = None):
        super().__init__(client, config=config)
        self._db = db

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        # number/site/lang 已由 handlers/scrape.py bind 到 structlog 上下文, 各条日志不再重复
        if self._db is None:
            self.logger.debug("disabled (no db), skip")
            return None

        language = options.language if options else None
        # 番号 → content_id 匹配是本源命中率的核心; DEBUG 级记录候选便于排查 miss
        self.logger.debug("query start", candidates=content_id_candidates(query.number))
        t0 = time.monotonic()
        try:
            async with self._db.session() as session:
                detail = await R18Repository(session).get_detail(query.number)
        except Exception as e:
            # 镜像未配置/未导入 / 表缺失 / 连接失败 - 降级为无结果, 不影响其它源
            self.logger.warning("query failed", error=str(e), duration_s=round(time.monotonic() - t0, 3))
            return None

        elapsed = round(time.monotonic() - t0, 3)
        if detail is None:
            # 匹配不到行: 镜像无此番号, 或番号变体未覆盖 - DEBUG 候选可佐证
            self.logger.info("miss", duration_s=elapsed)
            return None

        result = to_metadata(detail, query.number, language)
        # 命中: 记录关联实体规模, 一眼看出聚合是否完整 (如演员/分类为 0 多半是 content_id join 不上)
        self.logger.debug(
            "detail aggregated",
            content_id=detail.video.content_id,
            dvd_id=detail.video.dvd_id,
            actresses=len(detail.actresses),
            actors=len(detail.actors),
            directors=len(detail.directors),
            categories=len(detail.categories),
            has_trailer=detail.trailer_url is not None,
        )
        self.logger.info("hit", content_id=detail.video.content_id, title=result.title, duration_s=elapsed)
        return result

    # _search/_scrape 为 ABC 抽象方法, 但本爬虫 override 了 fetch() 不会用到它们.
    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None
