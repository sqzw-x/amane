"""多源字段级聚合引擎 - 建图 / 波次执行 / 增量合并.

建图 (build_graph):
  1. compute_waves 生成波次结构 (含语言合并优化).
  2. 实例化 FetchNode, 按字段优先级链挂载 covers 与 fallback 边.
  3. 输出 FetchGraph - 一张完整的静态依赖图.

执行 (execute_graph):
  1. 按波次遍历, 每波仅调度仍有待处理字段的节点; 不在 crawlers 映射中的站点跳过.
  2. 并行抓取, 失败/空值沿 fallback 边传播, 成功沿 fallback 边剪枝.
  3. 后处理扫描: 同波 fallback 已执行 → 立即消费; 收集字段 (URL/score/extrafanart) 累积所有已执行节点.
  4. 每波注入 partial_result 到爬虫查询, 供高级爬虫使用.

编排 (aggregate):
  build_graph → execute_graph → AggregateResult.
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
from ..crawlers.models import FetchOptions, MediaMetadata, SearchQuery
from ..crawlers.site_roles import MULTI_LANGUAGE_SOURCE_IDS
from ..enums import Language, MetadataField, SiteName
from ..observability import current, invoke_source
from .models import AggregatedMetadata, AggregateResult, SourcedScore

type ProgressCallback = Callable[[int, int, str], Coroutine[Any, Any, None]]

# 标量字段 - 最终按优先级选单值.
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

# 必填标量字段 - 空值触发 fallback
REQUIRED_SCALAR_FIELDS: frozenset[MetadataField] = frozenset({MetadataField.TITLE})

# MediaMetadata -> Metadata DB 字段名映射 (raw 中的字段名与 DB 列名不一致的)
RAW_TO_DB_FIELD: dict[str, str] = {
    "extrafanart": "extrafanart_urls",
    "score": "scores",
    "external_id": "external_ids",
    "source_url": "source_urls",
}

# SCALAR_FIELDS 的字符串集合 (快速查找)
SCALAR_FIELD_NAMES: frozenset[str] = frozenset(SCALAR_FIELDS)

# URL 字段 - 收集所有已 fetch 站点的值. (字段 -> AggregatedMetadata dst 属性名)
URL_FIELD_MAP: dict[MetadataField, str] = {
    MetadataField.POSTER_URLS: "poster_urls",
    MetadataField.THUMB_URLS: "thumb_urls",
    MetadataField.TRAILER_URLS: "trailer_urls",
}

# 所有参与"优先级驱动请求"的字段
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

    prefer 与 route 求交后前置, 其余 route 站点保序接上.
    未覆盖字段直接使用 route.
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
    """爬虫的结构化类型 - 满足 fetch() 接口即可."""

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
    """对指定番号执行字段级多源聚合 (DAG 图执行 + 增量合并)."""
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
    """已满足的标量字段数 (进度分子)."""
    return sum(1 for f in SCALAR_FIELDS if f not in unsatisfied)


@dataclass
class ExecutionState:
    """执行过程中的可变状态."""

    number: str

    fetched: dict[SourceKey, MediaMetadata | None] = _f(default_factory=dict)
    failed: list[SourceKey] = _f(default_factory=list)
    sites_queried: list[SourceKey] = _f(default_factory=list)
    result: AggregatedMetadata = _f(default_factory=lambda: AggregatedMetadata(number=""))

    # 去重: 已从哪些 (ck, field) 收集过值 (URL/score/extrafanart)
    collected: set[tuple[SourceKey, MetadataField]] = _f(default_factory=set)

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
    """执行静态抓取图: 按波次调度, 沿 fallback 边增量合并.

    on_progress: 每波结束后回调 (已满足标量字段数, 标量字段总数, message).
    URL/收集类字段只累积不计入进度.
    """
    snapshots = db_cache or {}
    state = ExecutionState(number=query.number)
    field_total = len(SCALAR_FIELDS)

    # unsatisfied 驱动请求: 标量字段满足后移除, URL/收集字段始终保留
    unsatisfied: set[MetadataField] = set(ALL_FIELDS)

    # crawlers 映射是可用集合. 禁用插件 / 未安装来源 / 构造失败不在其中:
    # 标成已处理空结果, 让字段链与 _collect_after_wave 沿 fallback 继续,
    # 且不进 failed / sites_queried, 也不走 invoke_source (否则 KeyError → unexpected).
    for node in graph.nodes:
        if node.site not in crawlers:
            state.fetched[node.cache_key] = None

    for wave_idx, wave in enumerate(graph.waves):
        # 1. 确定活跃节点
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
                    # 可选字段: 爬虫成功但值为空 → 接受空值, 不再 fallback
                    _fill_scalar(state.result, field, data, ck)
                    unsatisfied.discard(field)
                    break

        active_nodes = [n for n in wave if n.cache_key in active]
        if not active_nodes:
            continue

        # 2. 注入中间结果
        partial = copy.copy(state.result) if wave_idx > 0 else None

        # 3. 并行抓取
        results = await asyncio.gather(
            *(_fetch_one(n, query, crawlers, snapshots, partial, state.fetched) for n in active_nodes)
        )

        for n, data in results:
            ck = n.cache_key
            state.fetched[ck] = data
            state.sites_queried.append(ck)
            if data is None:
                state.failed.append(ck)

        # 4. 后处理扫描
        _collect_after_wave(graph, state, unsatisfied)

        if on_progress is not None:
            sites = ", ".join(n.cache_key for n in active_nodes)
            await on_progress(_scalar_progress(unsatisfied), field_total, sites)

    return state


def _resolve_lang(
    site: str,
    field: MetadataField,
    field_language: FieldLanguage,
    multi_lang_fields: frozenset[MetadataField] = LANG_METADATA_FIELD_SET,
    multi_lang_sites: frozenset[SourceName] = MULTI_LANGUAGE_SOURCE_IDS,
) -> Language | None:
    """仅在字段和站点均支持多语言时返回语言, 否则返回 None."""
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
    """将字段级站点优先级展开为波次列表 (每波可并行).

    语言合并优化: 若某字段需要 (site, lang) 而另一字段仅需 (site, None),
    则将前者覆盖后者 - 一次带语言请求同时满足两者.
    """
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
    """DAG 中的一个抓取操作.

    site + lang 唯一确定一次抓取 (cache_key).
    covers: 本节点"原生"负责的字段 (在该字段优先级链中, 本节点是首个未被前驱覆盖的).
    fallback: 字段 → 兜底节点. 本节点无法提供该字段值时, 由哪个节点接替.
    """

    site: str
    lang: Language | None
    wave: int  # 所属波次 (拓扑层)

    covers: list[MetadataField] = _f(default_factory=list)
    fallback: dict[MetadataField, FetchNode | None] = _f(default_factory=dict)

    @property
    def cache_key(self) -> SourceKey:
        return _cache_key(self.site, self.lang)


@dataclass
class FetchGraph:
    """完整的静态抓取计算图."""

    nodes: list[FetchNode]
    waves: list[list[FetchNode]]  # 拓扑分层 (层内可并行)
    field_chains: dict[MetadataField, list[FetchNode]]  # 每字段的完整优先级链

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
    """从优先级配置生成完整静态抓取图.

    Phase 1: compute_waves 生成波次.
    Phase 2: 实例化节点, 注册到 registry.
    Phase 3: 为每个字段沿优先级链构建节点链, 匹配到 registry 中的最佳节点.
    Phase 4: 回填 covers 与 fallback 边.
    """
    waves = compute_waves(field_priority, field_language, multi_lang_fields, multi_lang_sites)

    # Phase 2: 实例化节点
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

    # Phase 3: 为每个字段构建优先级链 (沿 field_priority 顺序, 匹配到 registry 节点)
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

    # Phase 4: 回填 covers 与 fallback
    for field, chain in field_chains.items():
        for i, node in enumerate(chain):
            if i == 0:
                node.covers.append(field)
            node.fallback[field] = chain[i + 1] if i + 1 < len(chain) else None

    return FetchGraph(nodes=list(registry.values()), waves=wave_nodes, field_chains=field_chains)


def _find_best_node(site: str, field_lang: Language | None, registry: dict[SourceKey, FetchNode]) -> FetchNode | None:
    """在 registry 中为 (site, field_lang) 找到最佳节点.

    若 field_lang 指定: 精确匹配 site:lang.
    若 field_lang 为 None: 优先 site (无语言), 回退到 site:* (任意语言节点).
    """
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
    """名称列表保序去重."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _sanitize_aggregated_lists(meta: AggregatedMetadata) -> None:
    """聚合完成后归一 list 标量: 同名重复视为噪声 (源站布局镜像 / 爬虫瑕疵), 不入库存."""
    meta.actors = _dedupe_names(meta.actors)
    meta.tags = _dedupe_names(meta.tags)
    meta.directors = _dedupe_names(meta.directors)


def _fill_scalar(
    result: AggregatedMetadata,
    field: MetadataField,
    data: MediaMetadata,
    source_key: SourceKey,
) -> None:
    """将 data 的 field 值填入 result (仅当尚未设置)."""
    if field in result.field_sources:
        return
    setattr(result, field, getattr(data, field))
    result.field_sources[field] = source_key


def _collect_after_wave(graph: FetchGraph, state: ExecutionState, unsatisfied: set[MetadataField]) -> None:
    """每波结束后扫描: 同波 fallback 立即消费 + 收集字段累积."""
    for field, chain in graph.field_chains.items():
        if field in URL_FIELD_MAP:
            for node in chain:
                ck = node.cache_key
                if ck not in state.fetched or (ck, field) in state.collected:
                    continue
                data = state.fetched[ck]
                if data is None:
                    continue
                value = getattr(data, field, None)
                if value:
                    dst = URL_FIELD_MAP[field]
                    urls = [value] if isinstance(value, str) else value
                    getattr(state.result, dst).extend(v for v in urls if v)
                    state.collected.add((ck, field))

        elif field == MetadataField.EXTRAFANART:
            for node in chain:
                ck = node.cache_key
                if ck not in state.fetched or (ck, field) in state.collected:
                    continue
                data = state.fetched[ck]
                if data is not None and data.extrafanart:
                    state.result.extrafanart_urls[ck] = data.extrafanart
                    state.collected.add((ck, field))

        elif field == MetadataField.SCORE:
            for node in chain:
                ck = node.cache_key
                if ck not in state.fetched or (ck, field) in state.collected:
                    continue
                data = state.fetched[ck]
                if data is not None and data.score is not None:
                    state.result.scores.append(SourcedScore(site=ck, score=data.score))
                    state.collected.add((ck, field))

        elif field in SCALAR_FIELDS:
            if field not in unsatisfied:
                continue
            for node in chain:
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
                    # 可选字段: 爬虫成功但值为空 → 接受空值
                    _fill_scalar(state.result, field, data, ck)
                    unsatisfied.discard(field)
                    break
                # REQUIRED field with empty value: don't break, continue to next node

    # 被动收集: external_id / source_url
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
    """抓取单个 (site, lang) 组合, 优先复用 db_cache 快照."""
    site, lang = node.site, node.lang
    bind_contextvars(site=site, lang=lang, number=query.number)

    q = copy.copy(query)
    q.partial_result = partial_result
    q.raw_results = dict(raw_results)
    options = FetchOptions(lang) if lang else None

    # 检查 db_cache 快照
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
