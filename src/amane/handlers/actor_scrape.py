import asyncio
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from structlog.contextvars import bind_contextvars

from ..aggregate import first_known_gender, merge_actor_metadata, merge_actor_scrape_result
from ..crawlers.actor import ActorFetcher, ActorMetadata, filter_sites_for_gender
from ..crawlers.site_roles import is_actor_image_site, is_actor_profile_site
from ..db.actor_person import (
    actor_to_aggregated,
    apply_aggregated_to_actor,
    filter_locked_person_data,
    locked_fields_of,
)
from ..enums import ActorGender, SiteName
from ..net.errors import FailureReason, SourceError
from ..observability import current, invoke_source
from ..observability.models import SiteOutcomeKind
from .models import ActorScrapePayload, ActorScrapeResult, CacheKind
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ..config import HotSettings
    from ..db.repository import Repository
    from ..media import ResourceStore
    from ..net.http import WebClient


# 仅「名字未命中」可以换下一个查找名; 其余失败属于站点级故障, 换名会向同一站点重复发出请求.
_LOOKUP_MISS_REASONS = frozenset({FailureReason.NOT_FOUND, FailureReason.NO_USABLE_METADATA})


class ActorCrawlerFactoryLike(Protocol):
    async def get_actor_crawlers(self, names: Iterable[str]) -> Mapping[str, ActorFetcher]: ...


class ActorScrapeHandler(TaskHandler[ActorScrapePayload, ActorScrapeResult]):
    """按 Actor 查找名顺序访问资料来源 / 头像来源, 以本次结果写回.

    站点之间并发, 站内按查找名串行; 性别未知时被性别裁掉的站暂缓, 由第一段学到的性别决定是否补查.
    """

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

    def failure_result(self, payload: ActorScrapePayload) -> ActorScrapeResult | None:
        """抓取之后崩掉时, 站点明细仍要能看到."""
        outcomes = current().site_outcomes(include_unqueried=True)
        if not outcomes:
            return None
        return ActorScrapeResult(
            actor_id=payload.actor_id,
            field_sources={},
            failed_sites=[row.site for row in outcomes if row.outcome is SiteOutcomeKind.FAILED],
            image_count=0,
            outcomes=outcomes,
        )

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
        all_profile_sites = [s for s in cfg.profile_sites if is_actor_profile_site(s)]
        all_image_sites = [s for s in cfg.image_sites if is_actor_image_site(s)]
        ignored = [
            *[s for s in cfg.profile_sites if not is_actor_profile_site(s)],
            *[s for s in cfg.image_sites if not is_actor_image_site(s)],
        ]
        if ignored:
            rec.warning("actor scrape ignored sites lacking capability", sites=list(ignored))
        configured = _unique_sites([*all_profile_sites, *all_image_sites])
        if not configured:
            return TaskResult(success=False, error="尚未配置演员刮削站点")

        gender = actor.gender
        # 性别已知时按该性别裁站; unknown 时其余站暂缓, 由第一段学到的性别决定是否补查.
        sites, gated = filter_sites_for_gender(configured, gender)
        deferred = gated if gender is ActorGender.UNKNOWN else []

        if not sites:
            return TaskResult(success=False, error=f"没有适用于性别 {gender} 的演员刮削站点")

        use_metadata_cache = CacheKind.metadata in payload.use_cache
        # CacheKind.trans 写入 payload 但不改变本任务行为; raw 快照只经已查站读取, 被裁站不消费.
        raw_cache = dict(actor.raw or {}) if use_metadata_cache else {}

        rec.info(
            "actor scrape started",
            name=actor.name,
            gender=gender,
            lookup_names=names,
            sites=list(sites),
            deferred_sites=list(deferred),
            use_cache=sorted(payload.use_cache),
        )

        results: dict[SiteName, ActorMetadata | None] = {}
        failed_sites: list[str] = []
        rec.update_summary(eligible_sites=list(sites), sites_queried=list(sites))

        # 分母取上限: 暂缓的站只在学到性别时才查询, 落空时由其后步骤补满, 进度不回退.
        progress_total = len(sites) + len(deferred) + 2
        done = 0

        async def _fetch_phase(phase: Sequence[SiteName]) -> None:
            nonlocal done
            crawlers = await self._factory.get_actor_crawlers(phase)

            async def _one(site: SiteName) -> tuple[SiteName, ActorMetadata | None]:
                nonlocal done
                meta, cache_hit = await _fetch_site(site, crawlers.get(site), names, raw_cache)
                done += 1
                await self.report_progress(done, progress_total, f"{'cached' if cache_hit else 'fetched'} {site}")
                return site, meta

            # 并发完成, 结果按配置顺序收集: failed_sites 与合并优先级都不随完成顺序变化.
            for site, meta in await asyncio.gather(*(_one(site) for site in phase)):
                results[site] = meta
                if meta is None:
                    failed_sites.append(site)

        await _fetch_phase(sites)

        # 段间: 用第一段学到的性别重判暂缓站; 未取得性别则维持不查.
        second: list[SiteName] = []
        learned = first_known_gender(results, profile_sites=all_profile_sites)
        if deferred and learned is not None:
            second, _ = filter_sites_for_gender(deferred, learned)
        if second:
            rec.info("actor scrape second phase", gender=learned, sites=list(second))
            rec.update_summary(eligible_sites=[*sites, *second], sites_queried=[*sites, *second])
            await _fetch_phase(second)

        queried = {*sites, *second}
        profile_sites = [s for s in all_profile_sites if s in queried]
        image_sites = [s for s in all_image_sites if s in queried]

        # 本次结果优先写回, 空位由库内值兜底.
        site_agg = merge_actor_metadata(results, profile_sites=profile_sites, image_sites=image_sites)
        existing_aliases = await self._repo.get_actor_aliases(payload.actor_id)
        merged = merge_actor_scrape_result(site_agg, actor_to_aggregated(actor))
        # AUTO 写入: 锁定字段保留库内值; 下载与任务结果同以过滤后集合为准, 不为注定丢弃的新图下载.
        merged = filter_locked_person_data(merged, locked=locked_fields_of(actor), current=actor_to_aggregated(actor))

        if cfg.download_images and merged.image_urls and self._web_client is not None:
            for url in merged.image_urls:
                await self._resource_store.acquire(url, self._web_client)
        await self.report_progress(progress_total - 1, progress_total, "images")

        apply_aggregated_to_actor(actor, merged)
        # 别名行整表替换为「既有行 + 站点名」并集 (去重/去展示名/锁判定在行写入层).
        saved = await self._repo.save_actor(actor, aliases=[*existing_aliases, *merged.aliases])
        if saved is None:
            return TaskResult(
                success=False,
                error=f"保存演员 {payload.actor_id} 失败",
                result=ActorScrapeResult(
                    actor_id=payload.actor_id,
                    field_sources=dict(merged.field_sources),
                    failed_sites=failed_sites,
                    image_count=len(merged.image_urls),
                    outcomes=current().site_outcomes(),
                ),
            )
        await self.report_progress(progress_total, progress_total, "saved")

        rec.info(
            "actor scrape completed",
            field_sources=merged.field_sources,
            failed_sites=failed_sites,
            skipped_sites_by_gender=[s for s in configured if s not in queried],
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


async def _fetch_site(
    site: SiteName,
    crawler: ActorFetcher | None,
    names: Sequence[str],
    raw_cache: Mapping[str, object],
) -> tuple[ActorMetadata | None, bool]:
    """单站获取. 缓存命中与爬虫缺失都不发请求; 其余按查找名逐个尝试. 返回结果与是否命中缓存."""
    rec = current()
    cached = _actor_from_raw(raw_cache.get(site))
    if cached is not None:
        rec.info("actor site cache hit", site=site)
        rec.note_cache_hit(site)
        return cached, True
    if crawler is None:
        rec.warning("actor crawler unavailable", site=site)
        rec.record_site_outcome(site=site, outcome=SiteOutcomeKind.FAILED, reason=FailureReason.CRAWLER_UNAVAILABLE)
        return None, False

    async def _by_lookup_name() -> ActorMetadata | None:
        last_error: SourceError | None = None
        for name in names:
            try:
                meta = await crawler.fetch(name)
            except SourceError as exc:
                if exc.reason not in _LOOKUP_MISS_REASONS:
                    raise
                last_error = exc
                continue
            if meta is not None:
                rec.info("actor site hit", site=site, lookup_name=name)
                return meta
        if last_error is not None:
            raise last_error
        return None

    return await invoke_source(site, _by_lookup_name), False


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
