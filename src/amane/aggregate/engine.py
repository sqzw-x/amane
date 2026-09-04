"""字段级多源聚合: 静态抓取图 + 按波次请求 + 标量当场短路 + 收集类字段结束时按链拼接.

不在 crawlers 映射中的站点标成已处理空结果, 不写入 failed / sites_queried, 也不调用 invoke_source.
"""

import asyncio
import copy
from collections import defaultdict
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as _f
from typing import Any, Protocol

from structlog.contextvars import bind_contextvars

from ..config.manager import LANG_METADATA_FIELD_SET
from ..crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..crawlers.site_roles import MULTI_LANGUAGE_SOURCE_IDS
from ..enums import ActorGender, Language, MetadataField, SiteName
from ..observability import current, invoke_source
from .models import AggregatedMetadata, AggregateResult, SourcedScore

type ProgressCallback = Callable[[int, int, str], Coroutine[Any, Any, None]]

SCALAR_FIELDS: list[MetadataField] = [
    MetadataField.TITLE,
    MetadataField.ACTORS,
    MetadataField.TAGS,
    MetadataField.RELEASE,
    MetadataField.RUNTIME,
    MetadataField.DIRECTORS,
    MetadataField.SERIES,
    MetadataField.STUDIO,
    MetadataField.PUBLISHER,
    MetadataField.PLOT,
]

# 空值触发 fallback.
REQUIRED_SCALAR_FIELDS: frozenset[MetadataField] = frozenset({MetadataField.TITLE})

RAW_TO_DB_FIELD: dict[str, str] = {
    "extrafanart": "extrafanart_urls",
    "score": "scores",
    "external_id": "external_ids",
    "source_url": "source_urls",
}

SCALAR_FIELD_NAMES: frozenset[str] = frozenset(SCALAR_FIELDS)

URL_FIELD_MAP: dict[MetadataField, str] = {
    MetadataField.POSTER_URLS: "poster_urls",
    MetadataField.THUMB_URLS: "thumb_urls",
    MetadataField.TRAILER_URLS: "trailer_urls",
}

ALL_FIELDS: list[MetadataField] = [
    *SCALAR_FIELDS,
    *URL_FIELD_MAP,
    MetadataField.EXTRAFANART,
    MetadataField.SCORE,
]

type Wave = dict[str, set[Language]]
type SourceName = str | SiteName
type FieldPriority = Mapping[MetadataField, Sequence[SourceName]]
type FieldLanguage = Mapping[MetadataField, Language]
type SourceKey = str


def compile_priority(
    route: Sequence[SourceName], prefer: Mapping[MetadataField, Sequence[SourceName]]
) -> defaultdict[MetadataField, list[str]]:
    """content_routes[type] 是资格真值: 链上只会出现 route 内的站.

    prefer 与 route 求交后前置, 其余 route 站点保序接上. 未覆盖字段直接使用 route.
    """
    route_list = [str(site) for site in route]
    route_set = set(route_list)

    def chain_for(preferred: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for site in preferred:
            if site in route_set and site not in seen:
                seen.add(site)
                out.append(site)
        for site in route_list:
            if site not in seen:
                seen.add(site)
                out.append(site)
        return out

    overrides = {field: chain_for([str(site) for site in sites]) for field, sites in prefer.items() if sites}
    return defaultdict(lambda: list(route_list), overrides)


class CrawlerLike(Protocol):
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None: ...


async def aggregate(
    query: SearchQuery,
    crawlers: Mapping[str, CrawlerLike],
    field_priority: FieldPriority,
    field_language: FieldLanguage | None = None,
    cache: Mapping[str, dict] | None = None,
    on_progress: ProgressCallback | None = None,
    multi_lang_sites: frozenset[SourceName] = MULTI_LANGUAGE_SOURCE_IDS,
) -> AggregateResult:
    fl = field_language or {}
    snapshots = cache or {}

    graph = build_graph(field_priority, fl, multi_lang_sites=multi_lang_sites)
    current().debug("fetch graph built", graph=str(graph))
    state = await execute_graph(graph, crawlers, query, snapshots, on_progress=on_progress)

    if not state.fetched:
        current().warning("no data fetched from any source")
        return AggregateResult(
            metadata=AggregatedMetadata(number=query.number),
            field_sources={},
            failed_sites=list(state.failed),
            sites_queried=state.sites_queried,
            raw={},
            log="",
        )

    raw: dict[str, dict] = {k: v.model_dump() for k, v in state.fetched.items() if v is not None}

    _sanitize_aggregated_lists(state.result)

    return AggregateResult(
        metadata=state.result,
        field_sources=state.result.field_sources,
        failed_sites=list(state.failed),
        sites_queried=state.sites_queried,
        raw=raw,
        log="",
    )


def _scalar_progress(unsatisfied: set[MetadataField]) -> int:
    return sum(1 for f in SCALAR_FIELDS if f not in unsatisfied)


@dataclass
class ExecutionState:
    number: str

    fetched: dict[SourceKey, MediaMetadata | None] = _f(default_factory=dict)
    failed: list[SourceKey] = _f(default_factory=list)
    sites_queried: list[SourceKey] = _f(default_factory=list)
    result: AggregatedMetadata = _f(default_factory=lambda: AggregatedMetadata(number=""))

    def __post_init__(self):
        if not self.result.number:
            self.result = AggregatedMetadata(number=self.number)


async def execute_graph(
    graph: FetchGraph,
    crawlers: Mapping[str, CrawlerLike],
    query: SearchQuery,
    db_cache: Mapping[SourceKey, dict] | None = None,
    on_progress: ProgressCallback | None = None,
) -> ExecutionState:
    """按波次请求; 标量沿 fallback 当场短路. URL / 评分 / 剧照在全部请求结束后按字段链拼接."""
    snapshots = db_cache or {}
    state = ExecutionState(number=query.number)
    field_total = len(SCALAR_FIELDS)

    # 标量满足后移除; URL / 收集字段始终保留.
    unsatisfied: set[MetadataField] = set(ALL_FIELDS)

    # 禁用插件 / 未安装来源 / 构造失败不在 crawlers 中: 标成已处理空结果,
    # 不写入 failed / sites_queried, 也不调用 invoke_source (否则 KeyError → unexpected).
    for node in graph.nodes:
        if node.site not in crawlers:
            state.fetched[node.cache_key] = None

    for wave_idx, wave in enumerate(graph.waves):
        # 本波仍有待处理字段的节点.
        active: set[SourceKey] = set()
        for field in list(unsatisfied):
            for node in graph.field_chains[field]:
                ck = node.cache_key
                if ck not in state.fetched:
                    active.add(ck)
                    break
                if field not in SCALAR_FIELDS:
                    continue
                data = state.fetched[ck]
                if data is None:
                    continue
                value = getattr(data, field, None)
                if value:
                    _fill_scalar(state.result, field, data, ck)
                    unsatisfied.discard(field)
                    break
                if field not in REQUIRED_SCALAR_FIELDS:
                    # 可选字段: 爬虫成功但值为空 → 接受空值, 不再 fallback.
                    _fill_scalar(state.result, field, data, ck)
                    unsatisfied.discard(field)
                    break
                # 必填字段空值不接受, 继续沿链回退.

        active_nodes = [n for n in wave if n.cache_key in active]
        if not active_nodes:
            continue

        # 注入已合并的中间结果, 供后续波次爬虫使用.
        partial = copy.copy(state.result) if wave_idx > 0 else None

        # 并行抓取.
        results = await asyncio.gather(
            *(_fetch_one(n, query, crawlers, snapshots, partial, state.fetched) for n in active_nodes)
        )

        for n, data in results:
            ck = n.cache_key
            state.fetched[ck] = data
            state.sites_queried.append(ck)
            if data is None:
                state.failed.append(ck)

        # 标量沿链当场定值, 供短路与后波 partial 使用.
        _collect_scalars_after_wave(graph, state, unsatisfied)

        if on_progress is not None:
            sites = ", ".join(n.cache_key for n in active_nodes)
            await on_progress(_scalar_progress(unsatisfied), field_total, sites)

    _assemble_collected(graph, state)
    _fill_actor_genders(state.result, state.fetched)
    return state


def _resolve_lang(
    site: str,
    field: MetadataField,
    field_language: FieldLanguage,
    multi_lang_fields: frozenset[MetadataField] = LANG_METADATA_FIELD_SET,
    multi_lang_sites: frozenset[SourceName] = MULTI_LANGUAGE_SOURCE_IDS,
) -> Language | None:
    lang = field_language.get(field)
    return lang if field in multi_lang_fields and site in multi_lang_sites else None


def _cache_key(site: str, lang: Language | None) -> SourceKey:
    return f"{site}:{lang}" if lang else site


def compute_waves(
    field_priority: FieldPriority,
    field_language: FieldLanguage,
    multi_lang_fields: frozenset[MetadataField] = LANG_METADATA_FIELD_SET,
    multi_lang_sites: frozenset[str] = MULTI_LANGUAGE_SOURCE_IDS,
) -> list[Wave]:
    """若某字段需要 (site, lang) 而另一字段仅需 (site, None), 前者覆盖后者: 一次带语言请求同时满足两者."""
    pri = {f: [str(site) for site in field_priority[f]] for f in ALL_FIELDS}
    waves: list[Wave] = []
    site_langs: dict[str, set[Language]] = {}
    site_no_lang_wave: dict[str, Wave] = {}
    while True:
        frontiers: Wave = {}
        end = True
        for f, v in pri.items():
            if not v:
                continue
            end = False
            s = v.pop(0)
            lang = _resolve_lang(s, f, field_language, multi_lang_fields, multi_lang_sites)

            if s not in site_langs:
                site_langs[s] = {lang} if lang else set()
                if not lang:
                    site_no_lang_wave[s] = frontiers
            elif lang is None or lang in site_langs[s]:
                continue
            else:
                site_langs[s].add(lang)
                if s in site_no_lang_wave:
                    site_no_lang_wave[s][s].add(lang)
                    del site_no_lang_wave[s]
                    continue

            frontiers.setdefault(s, set())
            if lang:
                frontiers[s].add(lang)

        if end:
            break
        if frontiers:
            waves.append(frontiers)

    return waves


@dataclass
class FetchNode:
    """site + lang 唯一确定一次抓取.

    covers: 优先级链中本节点是首个未被前驱覆盖的字段.
    fallback: 本节点无法提供该字段值时接替的节点.
    """

    site: str
    lang: Language | None
    wave: int

    covers: list[MetadataField] = _f(default_factory=list)
    fallback: dict[MetadataField, FetchNode | None] = _f(default_factory=dict)

    @property
    def cache_key(self) -> SourceKey:
        return _cache_key(self.site, self.lang)


@dataclass
class FetchGraph:
    nodes: list[FetchNode]
    waves: list[list[FetchNode]]
    field_chains: dict[MetadataField, list[FetchNode]]

    def __str__(self) -> str:
        lines = []
        for i, wave in enumerate(self.waves):
            lines.append(f"Wave {i}:")
            for node in wave:
                lines.append(f"  - {node.cache_key}")
                lines.append("    fallback:")
                lines.extend(f"      {f} -> {n.cache_key}" for f in ALL_FIELDS if (n := node.fallback.get(f)))
        return "\n".join(lines)


def build_graph(
    field_priority: FieldPriority,
    field_language: FieldLanguage,
    multi_lang_fields: frozenset[MetadataField] = LANG_METADATA_FIELD_SET,
    multi_lang_sites: frozenset[SourceName] = MULTI_LANGUAGE_SOURCE_IDS,
) -> FetchGraph:
    waves = compute_waves(field_priority, field_language, multi_lang_fields, multi_lang_sites)

    # 实例化节点并注册.
    registry: dict[SourceKey, FetchNode] = {}
    wave_nodes: list[list[FetchNode]] = []
    for wi, wave in enumerate(waves):
        wns: list[FetchNode] = []
        for site, langs in wave.items():
            for lang in langs or {None}:
                ck = _cache_key(site, lang)
                node = registry.setdefault(ck, FetchNode(site=site, lang=lang, wave=wi))
                wns.append(node)
        wave_nodes.append(wns)

    # 按字段沿优先级链匹配节点.
    field_chains: dict[MetadataField, list[FetchNode]] = {}
    for field in ALL_FIELDS:
        chain: list[FetchNode] = []
        seen: set[SourceKey] = set()
        for site in field_priority[field]:
            field_lang = _resolve_lang(site, field, field_language, multi_lang_fields, multi_lang_sites)
            node = _find_best_node(site, field_lang, registry)
            if node and node.cache_key not in seen:
                chain.append(node)
                seen.add(node.cache_key)
        field_chains[field] = chain

    # 回填 covers 与 fallback 边.
    for field, chain in field_chains.items():
        for i, node in enumerate(chain):
            if i == 0:
                node.covers.append(field)
            node.fallback[field] = chain[i + 1] if i + 1 < len(chain) else None

    return FetchGraph(nodes=list(registry.values()), waves=wave_nodes, field_chains=field_chains)


def _find_best_node(site: str, field_lang: Language | None, registry: dict[SourceKey, FetchNode]) -> FetchNode | None:
    # field_lang 为 None 时优先无语言节点, 再回退到任意语言节点.
    if field_lang:
        return registry.get(_cache_key(site, field_lang))
    if site in registry:
        return registry[site]
    for lang in Language:
        key = _cache_key(site, lang)
        if key in registry:
            return registry[key]
    return None


def _dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _dedupe_film_actors(actors: list[FilmActor]) -> list[FilmActor]:
    seen: dict[str, FilmActor] = {}
    order: list[str] = []
    for item in actors:
        if not item.name:
            continue
        existing = seen.get(item.name)
        if existing is None:
            seen[item.name] = item.model_copy()
            order.append(item.name)
        elif existing.gender == ActorGender.UNKNOWN and item.gender != ActorGender.UNKNOWN:
            seen[item.name] = item.model_copy()
    return [seen[name] for name in order]


def _fill_actor_genders(result: AggregatedMetadata, fetched: Mapping[SourceKey, MediaMetadata | None]) -> None:
    """名单已锁定后, 按展示名从各源填空性别. 不改名单与顺序."""
    by_name = {item.name: item for item in result.actors}
    for data in fetched.values():
        if data is None:
            continue
        for src in data.actors:
            dest = by_name.get(src.name)
            if dest is None:
                continue
            if dest.gender == ActorGender.UNKNOWN and src.gender != ActorGender.UNKNOWN:
                dest.gender = src.gender


def _sanitize_aggregated_lists(meta: AggregatedMetadata) -> None:
    # 同名重复视为噪声 (源站布局镜像 / 爬虫瑕疵), 不入库存.
    meta.actors = _dedupe_film_actors(meta.actors)
    meta.tags = _dedupe_names(meta.tags)
    meta.directors = _dedupe_names(meta.directors)


def _fill_scalar(
    result: AggregatedMetadata,
    field: MetadataField,
    data: MediaMetadata,
    source_key: SourceKey,
) -> None:
    if field in result.field_sources:
        return
    if field == MetadataField.ACTORS:
        result.actors = [item.model_copy() for item in data.actors]
    else:
        setattr(result, field, getattr(data, field))
    result.field_sources[field] = source_key


def _collect_scalars_after_wave(graph: FetchGraph, state: ExecutionState, unsatisfied: set[MetadataField]) -> None:
    """沿字段链定值标量. 尚未请求的节点中断该字段, 不取后面已返回的站."""
    for field in SCALAR_FIELDS:
        if field not in unsatisfied:
            continue
        for node in graph.field_chains[field]:
            ck = node.cache_key
            if ck not in state.fetched:
                break
            data = state.fetched[ck]
            if data is None:
                continue
            value = getattr(data, field, None)
            if value:
                _fill_scalar(state.result, field, data, ck)
                unsatisfied.discard(field)
                break
            if field not in REQUIRED_SCALAR_FIELDS:
                _fill_scalar(state.result, field, data, ck)
                unsatisfied.discard(field)
                break


def _assemble_collected(graph: FetchGraph, state: ExecutionState) -> None:
    """按各字段站点顺序拼接已抓结果. 空值与未返回的站跳过, 不改变相对顺序."""
    for field, dst in URL_FIELD_MAP.items():
        urls: list[str] = []
        for node in graph.field_chains[field]:
            data = state.fetched.get(node.cache_key)
            if data is None:
                continue
            value = getattr(data, field, None)
            if not value:
                continue
            items = [value] if isinstance(value, str) else value
            urls.extend(v for v in items if v)
        setattr(state.result, dst, urls)

    extrafanart: dict[str, list[str]] = {}
    for node in graph.field_chains[MetadataField.EXTRAFANART]:
        ck = node.cache_key
        data = state.fetched.get(ck)
        if data is not None and data.extrafanart:
            extrafanart[ck] = data.extrafanart
    state.result.extrafanart_urls = extrafanart

    scores: list[SourcedScore] = []
    for node in graph.field_chains[MetadataField.SCORE]:
        ck = node.cache_key
        data = state.fetched.get(ck)
        if data is not None and data.score is not None:
            scores.append(SourcedScore(site=ck, score=data.score))
    state.result.scores = scores

    for ck, data in state.fetched.items():
        if data is None:
            continue
        if data.external_id and ck not in state.result.external_ids:
            state.result.external_ids[ck] = data.external_id
        if data.source_url and ck not in state.result.source_urls:
            state.result.source_urls[ck] = data.source_url


async def _fetch_one(
    node: FetchNode,
    query: SearchQuery,
    crawlers: Mapping[str, CrawlerLike],
    db_cache: Mapping[SourceKey, dict],
    partial_result: AggregatedMetadata | None,
    raw_results: dict[SourceKey, MediaMetadata | None],
) -> tuple[FetchNode, MediaMetadata | None]:
    site, lang = node.site, node.lang
    bind_contextvars(site=site, lang=lang, number=query.number)

    q = copy.copy(query)
    q.partial_result = partial_result
    q.raw_results = dict(raw_results)
    options = FetchOptions(lang) if lang else None

    # 优先复用 db_cache 快照.
    cached = None
    if lang:
        cached = db_cache.get(_cache_key(site, lang))
    else:
        for key_candidate in (site, *(f"{site}:{lang}" for lang in Language)):
            if key_candidate in db_cache:
                cached = db_cache[key_candidate]
                break

    if cached:
        try:
            meta = MediaMetadata(**cached)
            current().note_cache_hit(node.cache_key)
            return node, meta
        except TypeError:
            current().warning("reuse snapshot failed, refetch", site=site, lang=lang)

    crawler = crawlers.get(site)
    if crawler is None:
        return node, None

    async def _fetch() -> MediaMetadata | None:
        return await crawler.fetch(q, options)

    return node, await invoke_source(node.cache_key, _fetch)
