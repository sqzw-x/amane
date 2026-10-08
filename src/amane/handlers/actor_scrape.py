from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from structlog.contextvars import bind_contextvars

from ..aggregate import merge_actor_metadata, merge_actor_rows_fill_empty
from ..crawlers.actor import ActorFetcher, ActorMetadata, filter_sites_for_gender
from ..crawlers.site_roles import is_actor_image_site, is_actor_profile_site
from ..db.actor_person import (
    actor_to_aggregated,
    apply_aggregated_to_actor,
    filter_locked_person_data,
    locked_fields_of,
)
from ..enums import SiteName
from ..net.errors import FailureReason, SourceError
from ..observability import current, invoke_source
from ..observability.models import SiteOutcomeKind
from .models import ActorScrapePayload, ActorScrapeResult, CacheKind
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ..config import HotSettings
    from ..db.repository import Repository
    from ..media import ResourceStore
    from ..net.http import WebClient


class ActorCrawlerFactoryLike(Protocol):
    async def get_actor_crawlers(self, names: Iterable[str]) -> Mapping[str, ActorFetcher]: ...


class ActorScrapeHandler(TaskHandler[ActorScrapePayload, ActorScrapeResult]):
    """按 Actor 查找名顺序访问资料来源 / 头像来源, 填空合并后写回."""

    def __init__(
        self,
        repo: Repository,
        factory: ActorCrawlerFactoryLike,
        resource_store: ResourceStore,
        pipeline_config: HotSettings,
        web_client: WebClient | None = None,
    ):
        super().__init__(payload_t=ActorScrapePayload, result_t=ActorScrapeResult)
        self._repo = repo
        self._factory = factory
        self._config = pipeline_config
        self._resource_store = resource_store
        self._web_client = web_client

    async def handle(self, payload: ActorScrapePayload) -> TaskResult[ActorScrapeResult]:
        bind_contextvars(actor_id=payload.actor_id)
        rec = current()

        # 校验 Actor 与站点资格.
        actor = await self._repo.get_actor(payload.actor_id)
        if actor is None:
            return TaskResult(success=False, error=f"演员 {payload.actor_id} 不存在")

        names = await self._repo.get_actor_lookup_names(payload.actor_id)
        if not names:
            return TaskResult(success=False, error=f"演员 {payload.actor_id} 没有可用于查找的名字")

        bind_contextvars(actor_name=actor.name)
        cfg = self._config.actor_scraping
        profile_sites = [s for s in cfg.profile_sites if is_actor_profile_site(s)]
        image_sites = [s for s in cfg.image_sites if is_actor_image_site(s)]
        ignored = [
            *[s for s in cfg.profile_sites if not is_actor_profile_site(s)],
            *[s for s in cfg.image_sites if not is_actor_image_site(s)],
        ]
        if ignored:
            rec.warning("actor scrape ignored sites lacking capability", sites=list(ignored))
        configured = _unique_sites([*profile_sites, *image_sites])
        if not configured:
            return TaskResult(success=False, error="尚未配置演员刮削站点")

        gender = actor.gender
        sites, skipped_by_gender = filter_sites_for_gender(configured, gender)
        profile_sites = [s for s in profile_sites if s in sites]
        image_sites = [s for s in image_sites if s in sites]

        if not sites:
            return TaskResult(success=False, error=f"没有适用于性别 {gender} 的演员刮削站点")

        use_metadata_cache = CacheKind.metadata in payload.use_cache
        # CacheKind.trans 写入 payload 但不改变本任务行为.
        # 仅对允许的站读 raw, 避免 male/unknown 误用女-only 历史快照.
        raw_cache = dict(actor.raw or {}) if use_metadata_cache else {}

        rec.info(
            "actor scrape started",
            name=actor.name,
            gender=gender,
            lookup_names=names,
            sites=list(sites),
            skipped_sites_by_gender=list(skipped_by_gender),
            use_cache=sorted(payload.use_cache),
        )

        crawlers = await self._factory.get_actor_crawlers(sites)
        results: dict[SiteName, ActorMetadata | None] = {}
        failed_sites: list[str] = []
        progress_total = len(sites) + 2
        rec.update_summary(eligible_sites=list(sites), sites_queried=list(sites))

        # 出站: 按查找名逐站获取; 缓存命中则跳过请求.
        for i, site in enumerate(sites):
            cached_payload = raw_cache.get(site) if use_metadata_cache else None
            if cached_payload is not None:
                cached_meta = _actor_from_raw(cached_payload)
                if cached_meta is not None:
                    results[site] = cached_meta
                    rec.info("actor site cache hit", site=site)
                    rec.note_cache_hit(site)
                    await self.report_progress(i + 1, progress_total, f"cached {site}")
                    continue

            crawler = crawlers.get(site)
            if crawler is None:
                results[site] = None
                failed_sites.append(site)
                rec.warning("actor crawler unavailable", site=site)
                rec.record_site_outcome(
                    site=site, outcome=SiteOutcomeKind.FAILED, reason=FailureReason.CRAWLER_UNAVAILABLE
                )
                await self.report_progress(i + 1, progress_total, f"fetched {site}")
                continue

            site_crawler = crawler
            site_key = site

            async def _fetch_actor(
                crawler: ActorFetcher = site_crawler, site_id: str = site_key
            ) -> ActorMetadata | None:
                last_error: SourceError | None = None
                for name in names:
                    try:
                        meta = await crawler.fetch(name)
                    except SourceError as exc:
                        last_error = exc
                        continue
                    if meta is not None:
                        rec.info("actor site hit", site=site_id, lookup_name=name)
                        return meta
                if last_error is not None:
                    raise last_error
                return None

            meta = await invoke_source(site_key, _fetch_actor)
            results[site] = meta
            if meta is None:
                failed_sites.append(site)
            await self.report_progress(i + 1, progress_total, f"fetched {site}")

        # 填空合并后写库.
        site_agg = merge_actor_metadata(results, profile_sites=profile_sites, image_sites=image_sites)
        existing_aliases = await self._repo.get_actor_aliases(payload.actor_id)
        merged = merge_actor_rows_fill_empty(actor_to_aggregated(actor), site_agg)
        # AUTO 写入: 锁定字段保留库内值; 下载与任务结果同以过滤后集合为准, 不为注定丢弃的新图下载.
        merged = filter_locked_person_data(merged, locked=locked_fields_of(actor), current=actor_to_aggregated(actor))

        if cfg.download_images and merged.image_urls and self._web_client is not None:
            for url in merged.image_urls:
                await self._resource_store.acquire(url, self._web_client)
        await self.report_progress(len(sites) + 1, progress_total, "images")

        apply_aggregated_to_actor(actor, merged)
        # 别名行整表替换为「既有行 + 站点名」并集 (去重/去展示名在行写入层).
        saved = await self._repo.save_actor(actor, aliases=[*existing_aliases, *merged.aliases])
        if saved is None:
            return TaskResult(success=False, error=f"保存演员 {payload.actor_id} 失败")
        await self.report_progress(progress_total, progress_total, "saved")

        rec.info(
            "actor scrape completed",
            field_sources=merged.field_sources,
            failed_sites=failed_sites,
            skipped_sites_by_gender=list(skipped_by_gender),
            image_count=len(merged.image_urls),
        )
        return TaskResult(
            success=True,
            result=ActorScrapeResult(
                actor_id=payload.actor_id,
                field_sources=dict(merged.field_sources),
                failed_sites=failed_sites,
                image_count=len(merged.image_urls),
                outcomes=current().site_outcomes(),
            ),
        )


def _actor_from_raw(payload: object) -> ActorMetadata | None:
    """从 Actor.raw 站点快照还原; 非法字段降级为未命中."""
    if not isinstance(payload, dict):
        return None
    try:
        return ActorMetadata.model_validate(payload)
    except ValidationError:
        return None


def _unique_sites(sites: list[SiteName]) -> list[SiteName]:
    seen: set[SiteName] = set()
    out: list[SiteName] = []
    for site in sites:
        if site in seen:
            continue
        seen.add(site)
        out.append(site)
    return out
