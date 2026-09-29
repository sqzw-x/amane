"""测试 amane.aggregate: 抓取图、两段执行、标量沿链取第一个非空值、聚合类字段按链拼接."""

import copy
from collections import defaultdict

import pytest

from amane.aggregate import (
    ALL_FIELDS,
    SCALAR_FIELDS,
    aggregate,
    build_graph,
    compile_priority,
    execute_graph,
)
from amane.aggregate.engine import _cache_key
from amane.aggregate.models import AggregatedMetadata
from amane.crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from amane.enums import ActorGender, Language, MetadataField, SiteName

# --- 辅助工具 ---

S1, S2, S3 = SiteName.JAVDB, SiteName.DMM, SiteName.JAVBUS
K1, K2, K3 = str(S1), str(S2), str(S3)
DB, DMM, BUS, OFF = SiteName.JAVDB, SiteName.DMM, SiteName.JAVBUS, SiteName.OFFICIAL
PLUGIN = "example.source"
PLUGIN_K = PLUGIN

IQQTV = SiteName.IQQTV
TITLE = MetadataField.TITLE
PLOT = MetadataField.PLOT
AGGREGATE_FIELDS = (
    MetadataField.POSTER_URLS,
    MetadataField.THUMB_URLS,
    MetadataField.TRAILER_URLS,
    MetadataField.EXTRAFANART,
    MetadataField.SCORE,
)


class TestCompilePriority:
    @pytest.mark.parametrize(
        ("desc", "route", "prefer", "field", "expected"),
        [
            ("无 prefer → 每字段 = route", [DB, DMM, BUS], {}, TITLE, [DB, DMM, BUS]),
            ("prefer ∩ route 前置", [DB, DMM, BUS], {TITLE: [BUS]}, TITLE, [BUS, DB, DMM]),
            ("prefer 中不在 route 的站丢弃", [DB, DMM], {TITLE: [IQQTV, DMM]}, TITLE, [DMM, DB]),
            ("prefer 全在 route 外 → 等于 route", [DB, DMM], {TITLE: [IQQTV]}, TITLE, [DB, DMM]),
            ("空 prefer 值忽略", [DB, DMM], {TITLE: []}, TITLE, [DB, DMM]),
            ("未覆盖字段用 route", [DB, DMM], {TITLE: [DMM]}, PLOT, [DB, DMM]),
            ("prefer 去重保序", [DB, DMM, BUS], {TITLE: [DMM, DMM, BUS]}, TITLE, [DMM, BUS, DB]),
        ],
        ids=[
            "no_prefer",
            "intersect_prepend",
            "drop_outside",
            "all_outside",
            "empty_prefer",
            "uncovered_field",
            "dedupe",
        ],
    )
    def test_chain(
        self,
        desc: str,
        route: list[SiteName],
        prefer: dict[MetadataField, list[SiteName]],
        field: MetadataField,
        expected: list[SiteName],
    ) -> None:
        chains = compile_priority(route, prefer)
        assert chains[field] == expected, desc

    def test_empty_route(self) -> None:
        chains = compile_priority([], {TITLE: [DB]})
        assert chains[TITLE] == []
        assert chains[PLOT] == []

    @pytest.mark.parametrize(
        ("desc", "route", "prefer", "exclude", "field", "expected"),
        [
            ("仅排除: 从 route 去掉", [DB, DMM, BUS], {}, {TITLE: [BUS]}, TITLE, [DB, DMM]),
            ("仅排除: 未覆盖字段仍是 route", [DB, DMM, BUS], {}, {TITLE: [BUS]}, PLOT, [DB, DMM, BUS]),
            ("排除 + 优先: 排除优先", [DB, DMM, BUS], {TITLE: [BUS]}, {TITLE: [BUS]}, TITLE, [DB, DMM]),
            ("排除 route 外的站无效", [DB, DMM], {}, {TITLE: [IQQTV]}, TITLE, [DB, DMM]),
            ("空排除忽略", [DB, DMM], {}, {TITLE: []}, TITLE, [DB, DMM]),
            ("优先前置后再排除", [DB, DMM, BUS], {TITLE: [BUS, DMM]}, {TITLE: [DMM]}, TITLE, [BUS, DB]),
            ("排除全部 → 空链", [DB, DMM], {}, {TITLE: [DB, DMM]}, TITLE, []),
        ],
        ids=[
            "exclude_from_route",
            "exclude_uncovered_intact",
            "exclude_wins_over_prefer",
            "exclude_outside_noop",
            "empty_exclude",
            "prefer_then_exclude",
            "exclude_all",
        ],
    )
    def test_exclude(
        self,
        desc: str,
        route: list[SiteName],
        prefer: dict[MetadataField, list[SiteName]],
        exclude: dict[MetadataField, list[SiteName]],
        field: MetadataField,
        expected: list[SiteName],
    ) -> None:
        chains = compile_priority(route, prefer, exclude)
        assert chains[field] == expected, desc


class MockCrawler:
    """返回预设结果, 记录调用次数."""

    def __init__(self, result: MediaMetadata | None = None):
        self._result = result
        self.fetch_calls: list[str] = []

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        self.fetch_calls.append(query.number if hasattr(query, "number") else query)
        return self._result


class FailingCrawler:
    """爬虫抛异常 - 模拟网络错误."""

    def __init__(self):
        self.fetch_calls: list[str] = []

    async def fetch(self, query, options=None) -> MediaMetadata | None:
        self.fetch_calls.append(query.number if hasattr(query, "number") else query)
        raise RuntimeError("Connection failed")


class RecordingCrawler:
    """记录收到的 SearchQuery 与 partial_result 引用 (用于验证两段执行)."""

    def __init__(self, result: MediaMetadata):
        self._result = result
        self.queries: list[SearchQuery] = []
        self.partials: list[AggregatedMetadata | None] = []
        self.options: list[FetchOptions | None] = []

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        self.queries.append(copy.copy(query))
        self.partials.append(query.partial_result)
        self.options.append(options)
        return self._result


class MutatingCrawler(RecordingCrawler):
    """拿到 partial_result 后改动它, 用于验证只读契约."""

    def __init__(self, result: MediaMetadata):
        super().__init__(result)
        self.observed_studio: str | None = None

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        result = await super().fetch(query, options)
        if query.partial_result is not None:
            self.observed_studio = query.partial_result.studio
            query.partial_result.studio = "MUTATED"
            query.partial_result.field_sources["studio"] = "mutated"
        return result


def _full_metadata(**overrides) -> MediaMetadata:
    defaults = {
        "number": "X",
        "title": "T",
        "actors": ["A"],
        "studio": "S",
        "release": "2025-01-01",
        "runtime": 90,
        "tags": ["T"],
        "thumb_urls": ["http://t.jpg"],
        "poster_urls": ["http://p.jpg"],
        "score": 7.0,
        "directors": ["D"],
        "series": "Ser",
        "publisher": "Pub",
        "plot": "Plot",
        "trailer_urls": ["http://tr.mp4"],
        "extrafanart": ["http://e.jpg"],
    }
    defaults.update(overrides)
    return MediaMetadata.model_validate(defaults)


# ============================================================
# build_graph - 节点与字段链
# ============================================================


class TestBuildGraph:
    def test_nodes_are_union_of_field_chains(self):
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})
        assert [n.cache_key for n in graph.nodes] == [K1, K2, K3]

    def test_field_chains_respect_priority_order(self):
        """field_chains 中节点顺序 = 字段的 site 优先级顺序."""
        fp = defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]})
        graph = build_graph(fp, {})

        title_chain = graph.field_chains[MetadataField.TITLE]
        assert [n.site for n in title_chain] == [BUS, DMM, DB]

        default_chain = graph.field_chains[MetadataField.STUDIO]
        assert [n.site for n in default_chain] == [DB, DMM, BUS]

    def test_same_site_different_langs_produce_distinct_nodes(self):
        """同站点不同语言 = 不同节点."""
        fp = defaultdict(lambda: [DB])
        fl = {MetadataField.TITLE: Language.ZH_CN, MetadataField.PLOT: Language.JP}
        mf = frozenset({MetadataField.TITLE, MetadataField.PLOT})
        ms = frozenset({DB})

        graph = build_graph(fp, fl, mf, ms)

        cks = {n.cache_key for n in graph.nodes}
        assert _cache_key(DB, Language.ZH_CN) in cks
        assert _cache_key(DB, Language.JP) in cks

    def test_language_node_serves_language_free_fields(self):
        """站点只要在任一字段上需要语言, 其余字段共用该带语言节点, 不额外请求."""
        fp = defaultdict(lambda: [DB])
        graph = build_graph(
            fp,
            {MetadataField.TITLE: Language.ZH_CN},
            frozenset({MetadataField.TITLE}),
            frozenset({DB}),
        )

        assert [n.cache_key for n in graph.nodes] == [f"{DB}:{Language.ZH_CN}"]
        assert [n.cache_key for n in graph.field_chains[MetadataField.PLOT]] == [f"{DB}:{Language.ZH_CN}"]

    def test_site_blacklisted_from_all_fields_has_no_node(self):
        fp = compile_priority([DB, DMM], {}, {field: [DB] for field in ALL_FIELDS})
        graph = build_graph(fp, {})
        assert [n.cache_key for n in graph.nodes] == [K2]

    def test_site_blacklisted_from_aggregate_fields_keeps_node(self):
        """只被聚合类字段黑名单的站点仍在标量链上, 因此仍是节点."""
        fp = compile_priority([DB, DMM], {}, {field: [DMM] for field in AGGREGATE_FIELDS})
        graph = build_graph(fp, {})
        assert [n.cache_key for n in graph.nodes] == [K1, K2]


# ============================================================
# execute_graph - 取值语义
# ============================================================


class TestExecuteGraph:
    @pytest.mark.asyncio
    async def test_scalars_take_first_non_empty_value(self):
        """所有站点成功 → 标量取链上第一个非空值, URL 按链收集."""
        fp = defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]})
        graph = build_graph(fp, {})

        crawlers = {
            K1: MockCrawler(MediaMetadata(number="X", title="T_DB", studio="S_DB", poster_urls=["http://db.jpg"])),
            K2: MockCrawler(MediaMetadata(number="X", title="T_DMM", studio="S_DMM", poster_urls=["http://dmm.jpg"])),
            K3: MockCrawler(MediaMetadata(number="X", title="T_BUS", studio="S_BUS", poster_urls=["http://bus.jpg"])),
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == "T_BUS"
        assert state.result.field_sources["title"] == "javbus"
        assert state.result.studio == "S_DB"
        assert state.result.field_sources["studio"] == "javdb"
        assert state.result.poster_urls == ["http://db.jpg", "http://dmm.jpg", "http://bus.jpg"]
        assert len(state.failed) == 0

    @pytest.mark.asyncio
    async def test_empty_value_falls_through_to_next_source(self):
        """站点已返回但字段为空 → 继续取链上下一站的值."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(MediaMetadata(number="X", title="T1", series=None, studio="S1", tags=[]))
        c2 = MockCrawler(
            MediaMetadata.model_validate(
                {"number": "X", "title": "T2", "series": "Ser2", "studio": "S2", "tags": ["t2"]}
            )
        )

        state = await execute_graph(graph, {K1: c1, K2: c2}, SearchQuery("X"))

        assert state.result.series == "Ser2"
        assert state.result.field_sources["series"] == "dmm"
        assert state.result.tags == ["t2"]
        assert state.result.field_sources["tags"] == "dmm"
        assert state.result.title == "T1"
        assert state.result.studio == "S1"

    @pytest.mark.asyncio
    async def test_all_values_empty_keeps_fields_unresolved(self):
        """全部来源为空 → 字段不定值, 不写 field_sources; 站点仍算已请求."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        crawlers = {K1: MockCrawler(MediaMetadata(number="X")), K2: MockCrawler(MediaMetadata(number="X"))}

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title is None
        assert state.result.field_sources == {}
        assert state.sites_queried == [K1, K2]
        assert state.failed == []

    @pytest.mark.asyncio
    async def test_failure_falls_back_to_next_priority(self):
        """javdb 失败 → 标量取 dmm 的值."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        crawlers = {
            K1: FailingCrawler(),
            K2: MockCrawler(result=MediaMetadata(number="X", title="FromDMM", studio="S2")),
            K3: MockCrawler(result=None),
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == "FromDMM"
        assert state.result.field_sources["title"] == "dmm"
        assert state.result.studio == "S2"
        assert K1 in state.failed
        assert K3 in state.failed

    @pytest.mark.asyncio
    async def test_external_ids_and_source_urls_collected(self):
        """external_id 和 source_url 从所有站点被动收集."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=MediaMetadata(number="X", external_id="id_a", source_url="http://a.com"))
        c2 = MockCrawler(result=MediaMetadata(number="X", external_id="id_b", source_url="http://b.com"))

        state = await execute_graph(graph, {K1: c1, K2: c2}, SearchQuery("X"))

        assert state.result.external_ids["javdb"] == "id_a"
        assert state.result.external_ids["dmm"] == "id_b"
        assert state.result.source_urls["javdb"] == "http://a.com"
        assert state.result.source_urls["dmm"] == "http://b.com"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("live", "expect_title", "expect_source", "expect_queried", "expect_failed"),
        [
            pytest.param(
                {"javdb": "FromJavDB"},
                "FromJavDB",
                "javdb",
                ["javdb"],
                [],
                id="disabled-plugin-falls-back",
            ),
            pytest.param({}, None, None, [], [], id="all-unavailable"),
            pytest.param(
                {"javdb": None, "dmm": "FromDMM"},
                "FromDMM",
                "dmm",
                ["javdb", "dmm"],
                ["javdb"],
                id="unavailable-then-miss-then-good",
            ),
        ],
    )
    async def test_unavailable_crawler_skipped(
        self,
        live: dict[str, str | None],
        expect_title: str | None,
        expect_source: str | None,
        expect_queried: list[str],
        expect_failed: list[str],
    ) -> None:
        """路由里有但 crawlers 映射没有的来源 (禁用插件等) 跳过, 不记失败, 沿链继续."""
        fp = defaultdict(lambda: [PLUGIN_K, DB, DMM])
        graph = build_graph(fp, {})
        crawlers = {
            name: MockCrawler(None if title is None else MediaMetadata(number="X", title=title))
            for name, title in live.items()
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == expect_title
        if expect_source is None:
            assert "title" not in state.result.field_sources
        else:
            assert state.result.field_sources["title"] == expect_source
        assert state.sites_queried == expect_queried
        assert state.failed == expect_failed
        assert PLUGIN_K not in state.failed
        assert PLUGIN_K not in state.sites_queried

    @pytest.mark.asyncio
    async def test_db_cache_hit_skips_crawler(self):
        """db_cache 快照命中 → 不调用爬虫, 直接复用."""
        fp = defaultdict(lambda: [DB])
        graph = build_graph(fp, {})

        crawler = MockCrawler(result=_full_metadata(number="X"))  # should not be called
        snapshot = _full_metadata(number="X", title="Cached").model_dump()

        state = await execute_graph(
            graph,
            {K1: crawler},
            SearchQuery("X"),
            db_cache={"javdb": snapshot},
        )

        assert len(crawler.fetch_calls) == 0  # 缓存命中
        assert state.result.title == "Cached"

    @pytest.mark.asyncio
    async def test_aggregate_field_blacklist_still_requests_site(self):
        """站点仅被聚合类字段黑名单: 仍被请求并写入 sites_queried, 该字段取值不受影响."""
        fp = compile_priority([DB, DMM], {}, {field: [DMM] for field in AGGREGATE_FIELDS})
        graph = build_graph(fp, {})

        c_db = MockCrawler(MediaMetadata(number="X", title="T_DB", poster_urls=[]))
        c_dmm = MockCrawler(MediaMetadata(number="X", poster_urls=["http://dmm/p.jpg"]))

        state = await execute_graph(graph, {K1: c_db, K2: c_dmm}, SearchQuery("X"))

        assert state.sites_queried == [K1, K2]
        assert state.result.poster_urls == []
        assert state.result.field_sources["title"] == "javdb"

    @pytest.mark.asyncio
    async def test_all_fail_returns_empty_result(self):
        """全部站点失败 → fetched 全 None, 但所有节点都已尝试."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        state = await execute_graph(graph, {K1: FailingCrawler(), K2: FailingCrawler()}, SearchQuery("X"))

        assert state.result.title is None
        assert len(state.failed) == 2
        assert len(state.sites_queried) == 2


# ============================================================
# execute_graph - 两段执行
# ============================================================


class TestTwoPhaseExecution:
    @pytest.mark.asyncio
    async def test_declared_source_runs_in_second_phase(self):
        """声明来源在第二段执行, 第一段来源拿不到 partial_result."""
        fp = defaultdict(lambda: [DB, DMM, OFF], {MetadataField.TITLE: [OFF]})
        graph = build_graph(fp, {})

        first = RecordingCrawler(_full_metadata(number="X", title="T_DB", studio="S_DB"))
        second = RecordingCrawler(_full_metadata(number="X", title="T_DMM", studio="S_DMM"))
        declared = RecordingCrawler(_full_metadata(number="X", title="T_OFF", studio="S_OFF"))

        state = await execute_graph(
            graph,
            {K1: first, K2: second, str(OFF): declared},
            SearchQuery("X"),
            deferred_sites=frozenset({OFF}),
        )

        assert first.partials == [None]
        assert second.partials == [None]
        assert declared.partials[0] is not None
        assert declared.partials[0].studio == "S_DB"
        # 链首是声明来源且尚未执行: 段间解析不越过它定值, 第二段结果不被丢弃.
        assert declared.partials[0].title is None
        assert state.result.title == "T_OFF"
        assert state.result.field_sources["title"] == "official"
        assert state.sites_queried == [K1, K2, str(OFF)]

    @pytest.mark.asyncio
    async def test_declared_source_at_route_head_blocks_then_wins(self):
        """路由整体前置声明来源: 段间所有字段被中断, 末次解析按链首取值."""
        fp = defaultdict(lambda: [OFF, DB, DMM])
        graph = build_graph(fp, {})

        first = RecordingCrawler(_full_metadata(number="X", title="T_DB", studio="S_DB", series="FrmDB"))
        declared = RecordingCrawler(_full_metadata(number="X", title="T_OFF", studio="S_OFF", series=None))

        state = await execute_graph(
            graph,
            {K1: first, str(OFF): declared},
            SearchQuery("X"),
            deferred_sites=frozenset({OFF}),
        )

        assert declared.partials[0] is not None
        assert declared.partials[0].title is None
        assert declared.partials[0].studio is None
        assert state.result.title == "T_OFF"
        assert state.result.studio == "S_OFF"
        # 声明来源留空的字段仍由第一段来源补上.
        assert state.result.series == "FrmDB"

    @pytest.mark.asyncio
    async def test_second_phase_nodes_share_one_partial_and_cannot_write_back(self):
        """同段共享同一个只读对象; 爬虫对它的改动不回写聚合结果."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        first = MockCrawler(_full_metadata(number="X", title="T_DB", studio="S_DB"))
        mutating = MutatingCrawler(_full_metadata(number="X", studio="S_DMM"))
        other = RecordingCrawler(_full_metadata(number="X", series="SerBUS"))

        state = await execute_graph(
            graph,
            {K1: first, K2: mutating, K3: other},
            SearchQuery("X"),
            deferred_sites=frozenset({DMM, BUS}),
        )

        assert mutating.partials[0] is other.partials[0]
        assert mutating.partials[0] is not None
        assert mutating.observed_studio == "S_DB"
        assert state.result.studio == "S_DB"
        assert state.result.field_sources["studio"] == "javdb"

    @pytest.mark.asyncio
    async def test_no_first_phase_gives_empty_partial(self):
        """路由只有声明来源: 第二段仍执行, partial 是空对象而非 None."""
        fp = defaultdict(lambda: [OFF])
        graph = build_graph(fp, {})

        declared = RecordingCrawler(_full_metadata(number="X", title="T_OFF"))

        state = await execute_graph(graph, {str(OFF): declared}, SearchQuery("X"), deferred_sites=frozenset({OFF}))

        assert declared.partials[0] is not None
        assert isinstance(declared.partials[0], AggregatedMetadata)
        assert declared.partials[0].title is None
        assert state.result.title == "T_OFF"

    @pytest.mark.asyncio
    async def test_no_second_phase_keeps_partial_none(self):
        """无声明来源: 全部节点在第一段, partial_result 恒为 None."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        first = RecordingCrawler(_full_metadata(number="X"))
        second = RecordingCrawler(_full_metadata(number="X"))

        await execute_graph(graph, {K1: first, K2: second}, SearchQuery("X"))

        assert first.partials == [None]
        assert second.partials == [None]

    @pytest.mark.asyncio
    async def test_declared_source_unavailable_is_premarked(self):
        """声明来源不在 crawlers: 预标空结果, 不请求也不记失败, 其余来源照常."""
        fp = defaultdict(lambda: [PLUGIN_K, DB])
        graph = build_graph(fp, {})

        live = RecordingCrawler(_full_metadata(number="X", title="FromJavDB"))

        state = await execute_graph(graph, {K1: live}, SearchQuery("X"), deferred_sites=frozenset({PLUGIN_K}))

        assert state.sites_queried == [K1]
        assert PLUGIN_K not in state.failed
        assert state.result.title == "FromJavDB"
        assert live.partials == [None]


# ============================================================
# aggregate - 端到端编排
# ============================================================


class TestAggregate:
    @pytest.mark.asyncio
    async def test_single_source_fills_fields(self):
        """单源成功 → 所有标量字段来自该源."""
        data = MediaMetadata.model_validate(
            {"number": "MIDV-123", "title": "Title", "actors": ["A"], "studio": "S", "score": 8.5}
        )
        result = await aggregate(SearchQuery("MIDV-123"), {K1: MockCrawler(result=data)}, defaultdict(lambda: [DB]))
        assert result.metadata.title == "Title"
        assert result.field_sources["title"] == "javdb"
        assert len(result.failed_sites) == 0

    @pytest.mark.asyncio
    async def test_failed_site_recorded(self):
        """失败站点记录在 failed_sites 中."""
        good = MockCrawler(result=MediaMetadata(number="X", title="Good"))
        result = await aggregate(SearchQuery("X"), {K1: FailingCrawler(), K2: good}, defaultdict(lambda: [DB, DMM]))
        assert result.metadata.title == "Good"
        assert "javdb" in result.failed_sites

    @pytest.mark.asyncio
    async def test_all_fail_returns_empty(self):
        """全部失败 → 空结果."""
        result = await aggregate(SearchQuery("X"), {K1: FailingCrawler()}, defaultdict(lambda: [DB]))
        assert result.metadata.title is None
        assert result.field_sources == {}
        assert result.raw == {}

    @pytest.mark.asyncio
    async def test_empty_scalars_keep_raw_for_handler(self):
        """标量全空但有海报 → field_sources 为空, raw 非空 (handler 的失败判据)."""
        crawler = MockCrawler(result=MediaMetadata(number="X", poster_urls=["http://p.jpg"]))
        result = await aggregate(SearchQuery("X"), {K1: crawler}, defaultdict(lambda: [DB]))
        assert result.field_sources == {}
        assert result.metadata.poster_urls == ["http://p.jpg"]
        assert result.raw

    @pytest.mark.asyncio
    async def test_raw_contains_fetched_snapshots(self):
        """raw 字段包含所有已抓取站点的快照."""
        a = MockCrawler(result=MediaMetadata(number="X", title="A"))
        result = await aggregate(SearchQuery("X"), {K1: a}, defaultdict(lambda: [DB]))
        assert "javdb" in result.raw
        assert result.raw["javdb"]["title"] == "A"

    @pytest.mark.asyncio
    async def test_unavailable_source_skipped_not_failed(self):
        """禁用/缺失来源不写入 failed_sites, 后续源仍可聚合."""
        result = await aggregate(
            SearchQuery("X"),
            {K1: MockCrawler(MediaMetadata(number="X", title="FromJavDB"))},
            defaultdict(lambda: [PLUGIN_K, DB]),
        )
        assert result.metadata.title == "FromJavDB"
        assert result.field_sources["title"] == "javdb"
        assert PLUGIN_K not in result.failed_sites
        assert PLUGIN_K not in result.sites_queried

    @pytest.mark.asyncio
    async def test_cache_reuse_avoids_requests(self):
        """cache 命中 → 不实际请求, 直接复用快照."""
        c1 = MockCrawler(result=_full_metadata(number="X"))  # won't be called
        snapshot = _full_metadata(number="X", title="Cached", studio="CS").model_dump()

        result = await aggregate(
            SearchQuery("X"),
            {K1: c1},
            defaultdict(lambda: [DB]),
            cache={"javdb": snapshot},
        )

        assert len(c1.fetch_calls) == 0
        assert result.metadata.title == "Cached"
        assert result.metadata.studio == "CS"
        assert "javdb" in result.raw

    @pytest.mark.asyncio
    async def test_plot_normalized_on_fetch(self):
        """长文本在聚合出口收成纯文本, raw 快照与字段值一致."""
        crawler = MockCrawler(result=MediaMetadata(number="X", title="T", plot="前戏<br>高潮<br><br>尾声 &amp; 至此"))

        result = await aggregate(SearchQuery("X"), {K1: crawler}, defaultdict(lambda: [DB]))

        assert result.metadata.plot == "前戏\n高潮\n\n尾声 & 至此"
        assert result.raw["javdb"]["plot"] == result.metadata.plot

    @pytest.mark.asyncio
    async def test_plot_normalized_on_cache_hit(self):
        """快照复用同样过归一: 旧快照里的 HTML 不会绕过出口."""
        crawler = MockCrawler(result=_full_metadata(number="X"))
        snapshot = _full_metadata(number="X", title="Cached", plot="旧<br>快照").model_dump()

        result = await aggregate(SearchQuery("X"), {K1: crawler}, defaultdict(lambda: [DB]), cache={"javdb": snapshot})

        assert len(crawler.fetch_calls) == 0
        assert result.metadata.plot == "旧\n快照"


# ============================================================
# 复杂场景: 混合优先级 + 聚合类字段拼接
# ============================================================


class TestComplexScenarios:
    @pytest.mark.asyncio
    async def test_per_field_priority_overrides(self):
        """不同字段不同优先级: title 偏 javbus, studio 偏 dmm."""
        fp = defaultdict(
            lambda: [DB, DMM, BUS],
            {
                MetadataField.TITLE: [BUS, DB, DMM],
                MetadataField.STUDIO: [DMM, DB, BUS],
            },
        )
        graph = build_graph(fp, {})

        crawlers = {
            K1: MockCrawler(result=MediaMetadata(number="X", title="T_DB", studio="S_DB")),
            K2: MockCrawler(result=MediaMetadata(number="X", title="T_DMM", studio="S_DMM")),
            K3: MockCrawler(result=MediaMetadata(number="X", title="T_BUS", studio="S_BUS")),
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == "T_BUS"
        assert state.result.field_sources["title"] == "javbus"
        assert state.result.studio == "S_DMM"
        assert state.result.field_sources["studio"] == "dmm"

    @pytest.mark.asyncio
    async def test_per_field_blacklist_skips_site(self):
        """title 排除 javdb 后走下一站; studio 仍可用 javdb."""
        fp = compile_priority([DB, DMM, BUS], {}, {MetadataField.TITLE: [DB]})
        graph = build_graph(fp, {})

        crawlers = {
            K1: MockCrawler(result=MediaMetadata(number="X", title="T_DB", studio="S_DB")),
            K2: MockCrawler(result=MediaMetadata(number="X", title="T_DMM", studio="S_DMM")),
            K3: MockCrawler(result=MediaMetadata(number="X", title="T_BUS", studio="S_BUS")),
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == "T_DMM"
        assert state.result.field_sources["title"] == "dmm"
        assert state.result.studio == "S_DB"
        assert state.result.field_sources["studio"] == "javdb"

    @pytest.mark.asyncio
    async def test_three_way_fallback_chain(self):
        """三站点优先级链: javdb → dmm → javbus, 前两个失败, 第三个救场."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        crawlers = {
            K1: MockCrawler(result=None),
            K2: MockCrawler(result=None),
            K3: MockCrawler(result=MediaMetadata(number="X", title="LastResort")),
        }

        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        assert state.result.title == "LastResort"
        assert state.result.field_sources["title"] == "javbus"
        assert K1 in state.failed
        assert K2 in state.failed
        assert K3 not in state.failed

    @pytest.mark.asyncio
    async def test_db_cache_partial_hit_mixed_with_live_fetch(self):
        """db_cache 部分命中 (javdb 快照复用), dmm 仍需真实请求."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c_javdb = MockCrawler(result=MediaMetadata(number="X", title="ShouldNotBeCalled"))
        c_dmm = MockCrawler(result=MediaMetadata(number="X", title="LiveDMM"))

        state = await execute_graph(
            graph,
            {K1: c_javdb, K2: c_dmm},
            SearchQuery("X"),
            db_cache={"javdb": _full_metadata(number="X", title="Cached").model_dump()},
        )

        assert len(c_javdb.fetch_calls) == 0
        assert state.result.title == "Cached"

    @pytest.mark.asyncio
    async def test_aggregate_fields_concatenate_in_chain_order(self):
        """聚合类字段按字段链顺序拼接, 与请求阶段无关."""
        fp = compile_priority(
            [DMM, DB, PLUGIN_K],
            {
                MetadataField.ACTORS: [PLUGIN_K, DB, DMM],
                MetadataField.SCORE: [PLUGIN_K, DB, DMM],
            },
        )
        graph = build_graph(fp, {})

        plugin_meta = MediaMetadata.model_validate(
            {
                "number": "NFDM-311",
                "title": "FromPlugin",
                "actors": ["P1"],
                "poster_urls": ["http://plugin/poster.jpg"],
                "thumb_urls": ["http://plugin/thumb.jpg"],
                "extrafanart": ["http://plugin/e1.jpg"],
                "score": 4.1,
            }
        )
        javdb_snap = MediaMetadata.model_validate(
            {
                "number": "NFDM-311",
                "title": "FromJavDB",
                "actors": ["J1"],
                "studio": "Freedom",
                "poster_urls": [],
                "thumb_urls": ["http://jdbstatic/cover.jpg"],
                "extrafanart": ["http://jdbstatic/e1.jpg"],
                "score": 4.36,
            }
        ).model_dump()

        state = await execute_graph(
            graph,
            {
                "dmm": MockCrawler(None),
                K1: MockCrawler(MediaMetadata(number="NFDM-311", title="ShouldNotFetch")),
                PLUGIN_K: MockCrawler(plugin_meta),
            },
            SearchQuery("NFDM-311"),
            db_cache={"javdb": javdb_snap},
        )

        assert state.result.title == "FromJavDB"
        assert state.result.field_sources["title"] == "javdb"
        assert [a.name for a in state.result.actors] == ["P1"]
        assert state.result.field_sources["actors"] == PLUGIN_K
        assert state.result.poster_urls == ["http://plugin/poster.jpg"]
        assert state.result.thumb_urls == ["http://jdbstatic/cover.jpg", "http://plugin/thumb.jpg"]
        assert list(state.result.extrafanart_urls) == ["javdb", PLUGIN_K]
        assert [s.site for s in state.result.scores] == [PLUGIN_K, "javdb"]
        assert [s.score for s in state.result.scores] == [4.1, 4.36]

    @pytest.mark.asyncio
    async def test_higher_priority_empty_url_does_not_occupy_slot(self):
        """图片链上更前的站该字段为空时, 列表从下一站有值处开始, 不留空位."""
        fp = compile_priority([DB, DMM, PLUGIN_K], {MetadataField.SCORE: [PLUGIN_K, DB, DMM]})
        graph = build_graph(fp, {})

        state = await execute_graph(
            graph,
            {
                K1: MockCrawler(
                    MediaMetadata(number="X", title="T", poster_urls=[], thumb_urls=["http://javdb/t.jpg"], score=1.0)
                ),
                "dmm": MockCrawler(
                    MediaMetadata(number="X", poster_urls=["http://dmm/p.jpg"], thumb_urls=[], score=None)
                ),
                PLUGIN_K: MockCrawler(
                    MediaMetadata(
                        number="X",
                        poster_urls=["http://plugin/p.jpg"],
                        thumb_urls=["http://plugin/t.jpg"],
                        score=9.0,
                    )
                ),
            },
            SearchQuery("X"),
        )

        assert state.result.poster_urls == ["http://dmm/p.jpg", "http://plugin/p.jpg"]
        assert state.result.thumb_urls == ["http://javdb/t.jpg", "http://plugin/t.jpg"]
        assert [s.site for s in state.result.scores] == [PLUGIN_K, "javdb"]


# ============================================================
# 进度回调与列表清理
# ============================================================


class TestProgressReporting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("desc", "deferred", "expect_calls"),
        [
            ("无第二段: 每段一次", frozenset(), 1),
            ("有第二段: 两段各一次", frozenset({DMM}), 2),
        ],
        ids=["single_phase", "two_phases"],
    )
    async def test_execute_graph_progress(self, desc: str, deferred: frozenset[SiteName], expect_calls: int):
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})
        events: list[tuple[int, int, str]] = []

        async def on_progress(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        data = _full_metadata(number="X")
        await execute_graph(
            graph,
            {K1: MockCrawler(data), K2: MockCrawler(data)},
            SearchQuery("X"),
            on_progress=on_progress,
            deferred_sites=deferred,
        )

        assert len(events) == expect_calls, desc
        assert all(total == len(SCALAR_FIELDS) for _, total, _ in events), desc
        assert all(message for _, _, message in events), "message should name fetched sites"
        currents = [current for current, _, _ in events]
        assert currents == sorted(currents)
        assert events[-1][0] == len(SCALAR_FIELDS)

    @pytest.mark.asyncio
    async def test_progress_counts_only_non_empty_scalars(self):
        """分子只计非空取值; 全部来源为空时保持 0."""
        fp = defaultdict(lambda: [DB])
        graph = build_graph(fp, {})
        events: list[tuple[int, int, str]] = []

        async def on_progress(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        await execute_graph(
            graph,
            {K1: MockCrawler(MediaMetadata(number="X", poster_urls=["http://p.jpg"]))},
            SearchQuery("X"),
            on_progress=on_progress,
        )

        assert events == [(0, len(SCALAR_FIELDS), K1)]

    @pytest.mark.asyncio
    async def test_aggregate_forwards_progress(self):
        """aggregate 将 on_progress 传到 execute_graph."""
        fp = defaultdict(lambda: [DB])
        events: list[tuple[int, int, str]] = []

        async def on_progress(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        await aggregate(
            SearchQuery("X"),
            {K1: MockCrawler(_full_metadata(number="X"))},
            fp,
            on_progress=on_progress,
        )

        assert events
        assert events[-1] == (len(SCALAR_FIELDS), len(SCALAR_FIELDS), "javdb")

    @pytest.mark.asyncio
    async def test_no_callback_is_silent(self):
        fp = defaultdict(lambda: [DB])
        graph = build_graph(fp, {})
        await execute_graph(graph, {K1: MockCrawler(_full_metadata(number="X"))}, SearchQuery("X"))

    @pytest.mark.asyncio
    async def test_list_scalar_dedupes_duplicate_names(self):
        """聚合返回前对 list 标量保序去重."""
        fp = defaultdict(lambda: [DMM])
        c = MockCrawler(
            result=MediaMetadata.model_validate(
                {
                    "number": "X",
                    "title": "T",
                    "actors": ["A", "A", "B"],
                    "tags": ["t1", "t1"],
                    "directors": ["D", "D"],
                }
            )
        )
        result = await aggregate(SearchQuery("X"), {K2: c}, fp)
        assert [a.name for a in result.metadata.actors] == ["A", "B"]
        assert result.metadata.tags == ["t1"]
        assert result.metadata.directors == ["D"]

    @pytest.mark.asyncio
    async def test_actor_gender_filled_from_other_fetched_source(self):
        """名单由第一源锁定; 性别由其它已抓源按名填空."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})
        c1 = MockCrawler(
            result=MediaMetadata.model_validate({"number": "X", "title": "T", "actors": ["Mei"], "studio": "S"})
        )
        c2 = MockCrawler(
            result=MediaMetadata(
                number="X",
                actors=[FilmActor(name="Mei", gender=ActorGender.FEMALE)],
                poster_urls=["http://p.jpg"],
            )
        )
        state = await execute_graph(graph, {K1: c1, K2: c2}, SearchQuery("X"))
        assert [a.name for a in state.result.actors] == ["Mei"]
        assert state.result.actors[0].gender is ActorGender.FEMALE
        assert state.result.field_sources["actors"] == "javdb"


# ============================================================
# 集成: cache 与 handler 判据的前置条件
# ============================================================


class TestFetchGraphIntegration:
    @pytest.mark.asyncio
    async def test_aggregate_with_cache_and_missing_source(self):
        """aggregate 配合 cache: javdb 快照命中, dmm 失败 → 部分成功."""
        fp = defaultdict(lambda: [DB, DMM])

        c_dmm = MockCrawler(result=None)

        result = await aggregate(
            SearchQuery("X"),
            {K1: MockCrawler(result=_full_metadata(number="X")), K2: c_dmm},
            fp,
            cache={"javdb": _full_metadata(number="X", title="CacheHit", studio="CS").model_dump()},
        )

        assert result.metadata.title == "CacheHit"
        assert result.metadata.studio == "CS"
        assert "dmm" in result.failed_sites
        assert len(c_dmm.fetch_calls) == 1
        assert result.raw
