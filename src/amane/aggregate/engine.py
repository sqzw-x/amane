"""字段级多源聚合.

建图: ``compile_priority`` 给出各字段的站点顺序, ``site + lang`` 唯一确定一次获取的节点;
字段链是聚合真值, 单源字段取值顺序与多源字段拼接顺序都由它决定.
执行: 未声明依赖的节点并发请求, 声明 ``SourceTrait.NEEDS_PARTIAL`` 的来源在第二段并发请求;
段间把已定值的单源字段交给第二段 (只读). 单源字段沿链取第一个非空值, 链上存在未处理节点时中断该字段.
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
from ..utils.text import normalize_long_text
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

type SourceName = str | SiteName
type FieldPriority = Mapping[MetadataField, Sequence[SourceName]]
type FieldLanguage = Mapping[MetadataField, Language]
type SourceKey = str


def compile_priority(
    route: Sequence[SourceName],
    prefer: Mapping[MetadataField, Sequence[SourceName]],
    exclude: Mapping[MetadataField, Sequence[SourceName]] | None = None,
) -> defaultdict[MetadataField, list[str]]:
    """content_routes[type] 是资格真值: 链上只会出现 route 内的站.

    prefer 与 route 求交后前置, 其余 route 站点保序接上. exclude 从该字段链上剔除;
    同一站同时出现在 prefer 与 exclude 时以 exclude 为准. 未覆盖字段直接使用 route.
    """
    route_list = [str(site) for site in route]
    route_set = set(route_list)
    banned: dict[MetadataField, frozenset[str]] = {
        field: frozenset(str(site) for site in sites) for field, sites in (exclude or {}).items() if sites
    }

    def chain_for(preferred: Sequence[str], skip: frozenset[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for site in preferred:
            if site in route_set and site not in seen and site not in skip:
                seen.add(site)
                out.append(site)
        for site in route_list:
            if site not in seen and site not in skip:
                seen.add(site)
                out.append(site)
        return out

    fields = set(prefer) | set(banned)
    overrides = {
        field: chain_for([str(site) for site in prefer.get(field, ())], banned.get(field, frozenset()))
        for field in fields
        if prefer.get(field) or field in banned
    }
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
    deferred_sites: frozenset[SourceName] = frozenset(),
) -> AggregateResult:
    fl = field_language or {}
    snapshots = cache or {}

    graph = build_graph(field_priority, fl, multi_lang_sites=multi_lang_sites)
    current().debug("fetch graph built", graph=str(graph))
    state = await execute_graph(
        graph, crawlers, query, snapshots, on_progress=on_progress, deferred_sites=deferred_sites
    )

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


def _resolved_scalar_count(result: AggregatedMetadata) -> int:
    return sum(1 for field in SCALAR_FIELDS if field in result.field_sources)


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
    deferred_sites: frozenset[SourceName] = frozenset(),
) -> ExecutionState:
    """两段并发请求; 段间给声明来源注入已定值的单源字段, 全部结束后按字段链拼接多源字段."""
    snapshots = db_cache or {}
    state = ExecutionState(number=query.number)
    deferred = {str(site) for site in deferred_sites}
    field_total = len(SCALAR_FIELDS)

    # 禁用插件 / 未安装来源 / 构造失败不在 crawlers 中: 标成已处理空结果,
    # 不写入 failed / sites_queried, 也不调用 invoke_source (否则 KeyError → unexpected).
    for node in graph.nodes:
        if node.site not in crawlers:
            state.fetched[node.cache_key] = None

    available = [node for node in graph.nodes if node.site in crawlers]
    first = [node for node in available if node.site not in deferred]
    second = [node for node in available if node.site in deferred]
    current().debug(
        "fetch phases",
        first=[node.cache_key for node in first],
        second=[node.cache_key for node in second],
    )

    await _fetch_phase(state, first, query, crawlers, snapshots, None)
    _resolve_scalars(graph, state)
    await _report_progress(on_progress, state, first, field_total)

    if second:
        # 链上未执行的节点会中断对应字段, 第二段结果不会被段间解析抢先定值.
        partial = copy.deepcopy(state.result)
        await _fetch_phase(state, second, query, crawlers, snapshots, partial)
        _resolve_scalars(graph, state)
        await _report_progress(on_progress, state, second, field_total)

    _assemble_aggregate_fields(graph, state)
    _fill_actor_genders(state.result, state.fetched)
    return state


async def _fetch_phase(
    state: ExecutionState,
    nodes: Sequence[FetchNode],
    query: SearchQuery,
    crawlers: Mapping[str, CrawlerLike],
    snapshots: Mapping[SourceKey, dict],
    partial_result: AggregatedMetadata | None,
) -> None:
    if not nodes:
        return
    results = await asyncio.gather(*(_fetch_one(node, query, crawlers, snapshots, partial_result) for node in nodes))
    for node, data in results:
        cache_key = node.cache_key
        state.fetched[cache_key] = data
        state.sites_queried.append(cache_key)
        if data is None:
            state.failed.append(cache_key)


async def _report_progress(
    on_progress: ProgressCallback | None,
    state: ExecutionState,
    nodes: Sequence[FetchNode],
    field_total: int,
) -> None:
    if on_progress is None or not nodes:
        return
    sites = ", ".join(node.cache_key for node in nodes)
    await on_progress(_resolved_scalar_count(state.result), field_total, sites)


def _resolve_scalars(graph: FetchGraph, state: ExecutionState) -> None:
    """沿字段链取第一个非空值. 链上仍有未处理节点时中断该字段, 不取后面已返回的站."""
    for field in SCALAR_FIELDS:
        if field in state.result.field_sources:
            continue
        for node in graph.field_chains[field]:
            cache_key = node.cache_key
            if cache_key not in state.fetched:
                break
            data = state.fetched[cache_key]
            if data is None:
                continue
            if getattr(data, field, None):
                _fill_scalar(state.result, field, data, cache_key)
                break


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


@dataclass
class FetchNode:
    """site + lang 唯一确定一次获取."""

    site: str
    lang: Language | None

    @property
    def cache_key(self) -> SourceKey:
        return _cache_key(self.site, self.lang)


@dataclass
class FetchGraph:
    nodes: list[FetchNode]
    field_chains: dict[MetadataField, list[FetchNode]]

    def __str__(self) -> str:
        return "\n".join(
            f"{field}: {' -> '.join(node.cache_key for node in chain)}" for field, chain in self.field_chains.items()
        )


def build_graph(
    field_priority: FieldPriority,
    field_language: FieldLanguage,
    multi_lang_fields: frozenset[MetadataField] = LANG_METADATA_FIELD_SET,
    multi_lang_sites: frozenset[SourceName] = MULTI_LANGUAGE_SOURCE_IDS,
) -> FetchGraph:
    # 节点集合由字段链的并集推导: 被全部字段排除的站点不产生节点.
    # 登记顺序 = 字段与优先级链上的首次出现顺序, 该顺序决定 sites_queried / failed 的写入顺序.
    # 站点只要在任一字段上需要语言, 该站统一用带语言节点 (一次请求同时满足两类字段).
    site_langs: dict[str, set[Language]] = defaultdict(set)
    for field in ALL_FIELDS:
        for site in field_priority[field]:
            lang = _resolve_lang(str(site), field, field_language, multi_lang_fields, multi_lang_sites)
            if lang is not None:
                site_langs[str(site)].add(lang)

    registry: dict[SourceKey, FetchNode] = {}
    for field in ALL_FIELDS:
        for site in field_priority[field]:
            name = str(site)
            langs: list[Language | None] = [lang for lang in Language if lang in site_langs[name]] or [None]
            for lang in langs:
                cache_key = _cache_key(name, lang)
                if cache_key not in registry:
                    registry[cache_key] = FetchNode(site=name, lang=lang)

    # 按字段沿优先级链匹配节点.
    field_chains: dict[MetadataField, list[FetchNode]] = {}
    for field in ALL_FIELDS:
        chain: list[FetchNode] = []
        seen: set[SourceKey] = set()
        for site in field_priority[field]:
            field_lang = _resolve_lang(str(site), field, field_language, multi_lang_fields, multi_lang_sites)
            node = _find_best_node(str(site), field_lang, registry)
            if node and node.cache_key not in seen:
                chain.append(node)
                seen.add(node.cache_key)
        field_chains[field] = chain

    return FetchGraph(nodes=list(registry.values()), field_chains=field_chains)


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
    if field == MetadataField.ACTORS:
        result.actors = [item.model_copy() for item in data.actors]
    else:
        setattr(result, field, getattr(data, field))
    result.field_sources[field] = source_key


def _assemble_aggregate_fields(graph: FetchGraph, state: ExecutionState) -> None:
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


def _normalize_source_text(meta: MediaMetadata) -> MediaMetadata:
    """长文本在进入聚合前收成纯文本.

    归一只有这一处, 因此字段选择、``raw`` 快照与 merge 拿到的都是同一份规范值;
    函数幂等, 与落库钩子重复调用安全.
    """
    meta.plot = normalize_long_text(meta.plot)
    return meta


async def _fetch_one(
    node: FetchNode,
    query: SearchQuery,
    crawlers: Mapping[str, CrawlerLike],
    db_cache: Mapping[SourceKey, dict],
    partial_result: AggregatedMetadata | None,
) -> tuple[FetchNode, MediaMetadata | None]:
    site, lang = node.site, node.lang
    bind_contextvars(site=site, lang=lang, number=query.number)

    q = copy.copy(query)
    q.partial_result = partial_result
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
            return node, _normalize_source_text(meta)
        except TypeError:
            current().warning("reuse snapshot failed, refetch", site=site, lang=lang)

    crawler = crawlers.get(site)
    if crawler is None:
        return node, None

    async def _fetch() -> MediaMetadata | None:
        return await crawler.fetch(q, options)

    fetched = await invoke_source(node.cache_key, _fetch)
    if fetched is None:
        return node, None
    return node, _normalize_source_text(fetched)
