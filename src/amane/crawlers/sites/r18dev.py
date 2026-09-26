"""查询离线 PostgreSQL 镜像, 不经 HTTP.

override ``fetch()``. PG 未配置 (``db is None``) 或查询失败时返回 None, 不中断多源聚合.
不允许把 ``RequestError`` 当作 None — 本源无 HTTP, 查询异常在本函数内降级.
"""

import time
from typing import TYPE_CHECKING, override

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome, SkipReason
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from ..r18dev import R18Repository, content_id_candidates, to_metadata

if TYPE_CHECKING:
    from ...config import SiteConfig
    from ..http import HttpClient
    from ..r18dev import R18Database


class R18DevCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        # base_url 仅占位 (本源不发 HTTP); 保留以满足注册表 / 限速器构建约定.
        return CrawlerProfile(name=SiteName.R18DEV, base_url="https://r18.dev", multi_language=True)

    def __init__(self, client: HttpClient, config: SiteConfig | None = None, db: R18Database | None = None):
        super().__init__(client, config=config)
        self._db = db

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """本源无 HTTP 上游: 数据来自用户自备的离线 PG 镜像, 没有可探测的站点入口."""
        return ConnectivityOutcome.skipped(SkipReason.NO_HTTP_UPSTREAM)

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        if self._db is None:
            self.logger.debug("disabled (no db), skip")
            return None

        language = options.language if options else None
        self.logger.debug("query start", candidates=content_id_candidates(query.number))
        t0 = time.monotonic()
        try:
            async with self._db.session() as session:
                detail = await R18Repository(session).get_detail(query.number)
        except Exception as e:
            # 镜像未导入 / 表缺失 / 连接失败: 降级为无结果, 不影响其它源.
            self.logger.warning("query failed", error=str(e), duration_s=round(time.monotonic() - t0, 3))
            return None

        elapsed = round(time.monotonic() - t0, 3)
        if detail is None:
            self.logger.info("miss", duration_s=elapsed)
            return None

        result = to_metadata(detail, query.number, language)
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

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None
