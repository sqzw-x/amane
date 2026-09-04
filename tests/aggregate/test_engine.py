"""测试 amane.aggregate: 静态抓取图、分波请求、标量短路、聚合类字段按链拼接."""

from collections import defaultdict

import pytest

from amane.aggregate import (
    SCALAR_FIELDS,
    aggregate,
    build_graph,
    compile_priority,
    compute_waves,
    execute_graph,
)
from amane.aggregate.engine import ProgressCallback, _cache_key
from amane.crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from amane.enums import ActorGender, Language, MetadataField, SiteName

# --- 辅助工具 ---

S1, S2, S3 = SiteName.JAVDB, SiteName.DMM, SiteName.JAVBUS
K1, K2, K3 = str(S1), str(S2), str(S3)
DB, DMM, BUS, OFF = SiteName.JAVDB, SiteName.DMM, SiteName.JAVBUS, SiteName.OFFICIAL
PLUGIN = "sample.javdbapi"

IQQTV = SiteName.IQQTV
TITLE = MetadataField.TITLE
PLOT = MetadataField.PLOT


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
    """记录收到的 SearchQuery 参数 (用于验证 partial_result 注入)."""

    def __init__(self, result: MediaMetadata):
        self._result = result
        self.queries: list[SearchQuery] = []
        self.options: list[FetchOptions | None] = []

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        import copy as _copy

        self.queries.append(_copy.copy(query))
        self.options.append(options)
        return self._result


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
# compute_waves - 波次划分 (与旧测试兼容)
# ============================================================


@pytest.mark.parametrize(
    "desc, fp, fl, mf, ms, expected",
    [
        (
            "简单场景1: 所有字段默认优先级, 无语言",
            defaultdict(lambda: [DB, DMM, BUS]),
            {},
            frozenset(),
            frozenset(),
            [{S1: set()}, {S2: set()}, {S3: set()}],
        ),
        (
            "简单场景2: title 倒序, 其余默认",
            defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]}),
            {},
            frozenset(),
            frozenset(),
            [{DB: set(), BUS: set()}, {DMM: set()}],
        ),
        (
            "综合场景: 多语言合并, 多字段不同优先级",
            defaultdict(
                lambda: [DB, DMM, BUS],
                {
                    MetadataField.TITLE: [DB, DMM, BUS],
                    MetadataField.PLOT: [DMM, DB, BUS],
                    MetadataField.STUDIO: [DB, BUS, DMM],
                    MetadataField.RELEASE: [BUS, DB, DMM],
                },
            ),
            {
                MetadataField.TITLE: Language.ZH_CN,
                MetadataField.PLOT: Language.JP,
                MetadataField.STUDIO: Language.ZH_TW,
                MetadataField.RELEASE: Language.ZH_CN,
            },
            frozenset({MetadataField.TITLE, MetadataField.PLOT, MetadataField.STUDIO}),
            frozenset({DB, DMM, BUS}),
            [
                {DB: {Language.ZH_CN, Language.ZH_TW}, DMM: {Language.JP}, BUS: {Language.ZH_TW}},
                {DB: {Language.JP}, DMM: {Language.ZH_CN}},
                {DMM: {Language.ZH_TW}, BUS: {Language.ZH_CN, Language.JP}},
            ],
        ),
        (
            "综合场景: 第四轮仍可产生波次 (OFFICIAL 跨轮)",
            defaultdict(
                lambda: [DB, DMM, BUS],
                {
                    MetadataField.TITLE: [OFF, DB, DMM, BUS],
                    MetadataField.PLOT: [DMM, DB, BUS],
                    MetadataField.STUDIO: [DB, BUS, DMM],
                    MetadataField.RELEASE: [BUS, DB, DMM],
                    MetadataField.SCORE: [BUS, OFF, DB, DMM],
                },
            ),
            {MetadataField.TITLE: Language.ZH_CN, MetadataField.PLOT: Language.JP},
            frozenset({MetadataField.TITLE, MetadataField.PLOT, MetadataField.STUDIO}),
            frozenset({DB, BUS, OFF}),
            [
                {OFF: {Language.ZH_CN}, DB: {Language.ZH_CN}, DMM: set(), BUS: {Language.JP}},
                {DB: {Language.JP}},
                {BUS: {Language.ZH_CN}},
            ],
        ),
    ],
)
def test_compute_waves(desc, fp, fl, mf, ms, expected):
    waves = compute_waves(fp, fl, mf, ms)
    assert waves == expected, f"Failed: {desc}"


# ============================================================
# build_graph - 图结构正确性
# ============================================================


class TestBuildGraph:
    def test_nodes_count_matches_waves(self):
        """节点数 = 波次中 (site, lang) 组合的总数."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})
        # 无语言 → 3 波, 每波 1 个节点
        assert len(graph.nodes) == 3
        assert len(graph.waves) == 3
        assert all(len(w) == 1 for w in graph.waves)

    def test_field_chains_respect_priority_order(self):
        """field_chains 中节点顺序 = 字段的 site 优先级顺序."""
        fp = defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]})
        graph = build_graph(fp, {})

        # title chain: BUS → DMM → DB
        title_chain = graph.field_chains[MetadataField.TITLE]
        assert [n.site for n in title_chain] == [BUS, DMM, DB]

        # default chain: DB → DMM → BUS
        default_chain = graph.field_chains[MetadataField.STUDIO]
        assert [n.site for n in default_chain] == [DB, DMM, BUS]

    def test_fallback_edges_form_correct_chain(self):
        """每个字段的 fallback 边构成正确的优先级链."""
        fp = defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]})
        graph = build_graph(fp, {})

        # title 的 fallback 链: N_BUS → N_DMM → N_DB → None
        title_nodes = graph.field_chains[MetadataField.TITLE]
        assert len(title_nodes) == 3
        assert title_nodes[0].fallback[MetadataField.TITLE] is title_nodes[1]
        assert title_nodes[1].fallback[MetadataField.TITLE] is title_nodes[2]
        assert title_nodes[2].fallback[MetadataField.TITLE] is None

    def test_covers_assigned_correctly(self):
        """每个节点的 covers 包含其原生负责的字段."""
        fp = defaultdict(lambda: [DB, DMM, BUS], {MetadataField.TITLE: [BUS, DMM, DB]})
        graph = build_graph(fp, {})

        # 波 0: DB (默认字段) + BUS (title)
        w0_nodes = {n.site: n for n in graph.waves[0]}
        assert MetadataField.TITLE in w0_nodes[BUS].covers
        assert MetadataField.STUDIO in w0_nodes[DB].covers
        # title 不应该出现在 DB 的 covers 中 (DB 是 title 的第三优先级)
        assert MetadataField.TITLE not in w0_nodes[DB].covers

    def test_same_site_different_langs_produce_distinct_nodes(self):
        """同站点不同语言 = 不同节点."""
        fp = defaultdict(lambda: [DB])
        fl = {MetadataField.TITLE: Language.ZH_CN, MetadataField.PLOT: Language.JP}
        mf = frozenset({MetadataField.TITLE, MetadataField.PLOT})
        ms = frozenset({DB})

        graph = build_graph(fp, fl, mf, ms)

        # 应该有 javdb:zh_cn 和 javdb:jp 两个节点
        cks = {n.cache_key for n in graph.nodes}
        assert _cache_key(DB, Language.ZH_CN) in cks
        assert _cache_key(DB, Language.JP) in cks


# ============================================================
# execute_graph - 执行正确性
# ============================================================


class TestExecuteGraph:
    @pytest.mark.asyncio
    async def test_all_success_merges_correctly(self):
        """所有站点成功 → 标量字段按优先级选取, URL 收集所有站点."""
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
    async def test_early_termination_when_all_scalar_satisfied(self):
        """波 0 全部满足标量字段 → 不再执行后续波 (但 URL 字段仍驱动)."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=_full_metadata(number="X"))  # wave 0: javdb
        c2 = MockCrawler(result=_full_metadata(number="X"))  # wave 1: dmm
        c3 = MockCrawler(result=_full_metadata(number="X"))  # wave 2: javbus

        crawlers = {K1: c1, K2: c2, K3: c3}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # javdb (wave 0) 返回了完整数据, dmm 和 javbus 仍为 URL 字段而执行
        assert len(c1.fetch_calls) == 1  # always called
        # dmm, javbus: called because URL fields drive additional waves
        # but if javdb provides all URL values, dmm/javbus still get called for URL collection
        assert len(state.sites_queried) == 3

    @pytest.mark.asyncio
    async def test_failure_falls_back_to_next_priority(self):
        """javdb 失败 → title (默认优先 DB) 回退到 dmm."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        fail = FailingCrawler()
        good = MockCrawler(result=MediaMetadata(number="X", title="FromDMM", studio="S2"))

        crawlers = {K1: fail, K2: good, K3: MockCrawler(result=None)}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # DB 失败 → title/studio 等回退到 DMM
        assert state.result.title == "FromDMM"
        assert state.result.field_sources["title"] == "dmm"
        assert state.result.studio == "S2"
        assert K1 in state.failed

    @pytest.mark.asyncio
    async def test_empty_required_field_falls_through_to_next(self):
        """站点成功但 REQUIRED 字段为空 → 回退到下一优先级."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        no_title = MockCrawler(result=MediaMetadata(number="X", title=None, studio="S1"))
        has_title = MockCrawler(result=MediaMetadata(number="X", title="T2", studio="S2"))

        crawlers = {K1: no_title, K2: has_title}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # DB 成功但 title 为 None → 回退到 DMM (title 是 REQUIRED)
        assert state.result.title == "T2"
        assert state.result.field_sources["title"] == "dmm"
        # studio 被 DB 满足 (不为空)
        assert state.result.field_sources["studio"] == "javdb"

    @pytest.mark.asyncio
    async def test_empty_optional_field_accepted_no_fallback(self):
        """站点成功但 OPTIONAL 字段为 None → 接受空值, 不回退."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=MediaMetadata(number="X", title="T1", series=None, studio="S1"))
        c2 = MockCrawler(result=MediaMetadata(number="X", title="T2", series="Ser", studio="S2"))

        crawlers = {K1: c1, K2: c2}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # DB 成功 → series=None 被接受, 不回退到 DMM
        assert state.result.series is None
        assert state.result.field_sources.get("series") == "javdb"
        # title 和 studio 也被 DB 满足
        assert state.result.title == "T1"
        assert state.result.studio == "S1"

    @pytest.mark.asyncio
    async def test_empty_optional_list_field_accepted_no_fallback(self):
        """OPTIONAL list 字段 (actors=[]) 被接受, 不回退."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=MediaMetadata(number="X", title="T1", actors=[], studio="S1"))
        c2 = MockCrawler(
            result=MediaMetadata.model_validate({"number": "X", "title": "T2", "actors": ["A1"], "studio": "S2"})
        )

        crawlers = {K1: c1, K2: c2}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # actors 是 OPTIONAL → 空列表被接受, 不回退
        assert state.result.actors == []
        assert state.result.field_sources.get("actors") == "javdb"
        # title 和 studio 被 DB 满足
        assert state.result.title == "T1"
        assert state.result.studio == "S1"

    @pytest.mark.asyncio
    async def test_optional_fields_satisfied_by_first_site_even_when_empty(self):
        """多个 OPTIONAL 字段为空 → 全被第一站点满足, 不回退."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(
            result=MediaMetadata(
                number="X",
                title="T1",
                studio="S1",
                series=None,
                tags=[],
                directors=[],
                actors=[],
                publisher=None,
                plot=None,
            )
        )
        c2 = MockCrawler(result=MediaMetadata(number="X", series="Ser2", tags=["T2"]))

        crawlers = {K1: c1, K2: c2}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # OPTIONAL 字段: 全被 javdb 满足 (即使为空)
        assert state.result.series is None
        assert state.result.tags == []
        assert state.result.directors == []
        assert state.result.actors == []
        assert state.result.publisher is None
        assert state.result.plot is None
        assert state.result.field_sources.get("series") == "javdb"
        assert state.result.title == "T1"
        assert state.result.studio == "S1"

    @pytest.mark.asyncio
    async def test_mixed_required_and_optional_fields(self):
        """REQUIRED 为空回退, OPTIONAL 为空不回退 (混合场景)."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=MediaMetadata(number="X", title=None, series=None, actors=[], studio="S1"))
        c2 = MockCrawler(
            result=MediaMetadata.model_validate(
                {"number": "X", "title": "T2", "series": "Ser2", "actors": ["A2"], "studio": "S2"}
            )
        )

        crawlers = {K1: c1, K2: c2}
        state = await execute_graph(graph, crawlers, SearchQuery("X"))

        # title 是 REQUIRED → DB 的 None 不满足, 回退到 DMM
        assert state.result.title == "T2"
        assert state.result.field_sources["title"] == "dmm"

        # series 是 OPTIONAL → DB 的 None 被接受, 不回退
        assert state.result.series is None
        assert state.result.field_sources["series"] == "javdb"

        # actors 是 OPTIONAL → DB 的 [] 被接受, 不回退
        assert state.result.actors == []
        assert state.result.field_sources["actors"] == "javdb"

        # studio 是 OPTIONAL → DB 的 "S1" 被接受
        assert state.result.studio == "S1"
        assert state.result.field_sources["studio"] == "javdb"

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
    async def test_partial_result_injected_after_first_wave(self):
        """首波 partial_result=None, 后续波注入已聚合的中间结果."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        r1 = RecordingCrawler(_full_metadata(number="X", title="First", plot=None))
        r2 = RecordingCrawler(_full_metadata(number="X", title="Second", plot="P2"))

        await execute_graph(graph, {K1: r1, K2: r2}, SearchQuery("X"))

        # 波 0 (javdb): partial_result 应为 None
        assert r1.queries[0].partial_result is None
        # 波 1 (dmm): partial_result 应包含 javdb 的结果
        assert r2.queries[0].partial_result is not None
        assert r2.queries[0].partial_result.title == "First"

    @pytest.mark.asyncio
    async def test_all_fail_returns_empty_result(self):
        """全部站点失败 → fetched 全 None, 但所有节点都已尝试."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        state = await execute_graph(graph, {K1: FailingCrawler(), K2: FailingCrawler()}, SearchQuery("X"))

        assert state.result.title is None
        assert len(state.failed) == 2
        assert len(state.sites_queried) == 2

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
        """路由里有但 crawlers 映射没有的来源 (禁用插件等) 跳过, 不记失败, 沿 fallback 继续."""
        plugin = "sample.javdbapi"
        fp = defaultdict(lambda: [plugin, DB, DMM])
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
        assert plugin not in state.failed
        assert plugin not in state.sites_queried

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

    @pytest.mark.asyncio
    async def test_raw_contains_fetched_snapshots(self):
        """raw 字段包含所有已抓取站点的 asdict 快照."""
        a = MockCrawler(result=MediaMetadata(number="X", title="A"))
        result = await aggregate(SearchQuery("X"), {K1: a}, defaultdict(lambda: [DB]))
        assert "javdb" in result.raw
        assert result.raw["javdb"]["title"] == "A"

    @pytest.mark.asyncio
    async def test_unavailable_source_skipped_not_failed(self):
        """禁用/缺失来源不写入 failed_sites, 后续源仍可聚合."""
        plugin = "sample.javdbapi"
        result = await aggregate(
            SearchQuery("X"),
            {K1: MockCrawler(MediaMetadata(number="X", title="FromJavDB"))},
            defaultdict(lambda: [plugin, DB]),
        )
        assert result.metadata.title == "FromJavDB"
        assert result.field_sources["title"] == "javdb"
        assert plugin not in result.failed_sites
        assert plugin not in result.sites_queried

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


# ============================================================
# MediaMetadata asdict 往返一致性 (cache 复用基础)
# ============================================================


# ============================================================
# 复杂场景: 混合优先级 + 同波 fallback
# ============================================================


class TestComplexScenarios:
    @pytest.mark.asyncio
    async def test_mixed_priorities_same_wave_fallback(self):
        """两个字段优先级互逆, 同波执行互为 fallback.

        title: [javdb, dmm]
        poster_url: [dmm, javdb]
        同波 {javdb, dmm}, 互为 fallback. 任意一方失败, 另一方已执行.
        """
        fp = defaultdict(lambda: [DB], {MetadataField.TITLE: [DB, DMM], MetadataField.POSTER_URLS: [DMM, DB]})
        graph = build_graph(fp, {})

        c_javdb = MockCrawler(result=MediaMetadata(number="X", title="FromJavDB", poster_urls=["http://db.jpg"]))
        c_dmm = MockCrawler(result=MediaMetadata(number="X", title="FromDMM", poster_urls=["http://dmm.jpg"]))

        state = await execute_graph(graph, {K1: c_javdb, K2: c_dmm}, SearchQuery("X"))

        assert state.result.title == "FromJavDB"
        assert state.result.field_sources["title"] == "javdb"
        assert state.result.poster_urls == ["http://dmm.jpg", "http://db.jpg"]

    @pytest.mark.asyncio
    async def test_url_drives_additional_wave_when_scalar_satisfied(self):
        """标量字段在波 0 满足, 但 poster_url 仅波 1 站点有 → 波 1 仍执行."""
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})

        # 波 0 javdb: 有所有标量字段, 无 poster_url
        c1 = MockCrawler(result=MediaMetadata(number="X", title="T1", studio="S1", poster_urls=[]))
        # 波 1 dmm: 有 poster_url
        c2 = MockCrawler(result=MediaMetadata(number="X", poster_urls=["http://only-dmm.jpg"]))

        state = await execute_graph(graph, {K1: c1, K2: c2}, SearchQuery("X"))

        assert state.result.title == "T1"
        assert len(c1.fetch_calls) == 1
        assert len(c2.fetch_calls) == 1  # dmm 为 poster_url 而执行
        assert state.result.poster_urls == ["http://only-dmm.jpg"]

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

        # javdb 命中缓存 → 不调用爬虫
        assert len(c_javdb.fetch_calls) == 0
        assert state.result.title == "Cached"

    @pytest.mark.asyncio
    async def test_three_way_fallback_chain(self):
        """三站点优先级链: javdb → dmm → javbus, 前两个失败, 第三个救场."""
        fp = defaultdict(lambda: [DB, DMM, BUS])
        graph = build_graph(fp, {})

        c1 = MockCrawler(result=None)  # javdb 失败
        c2 = MockCrawler(result=None)  # dmm 失败
        c3 = MockCrawler(result=MediaMetadata(number="X", title="LastResort"))

        state = await execute_graph(graph, {K1: c1, K2: c2, K3: c3}, SearchQuery("X"))

        assert state.result.title == "LastResort"
        assert state.result.field_sources["title"] == "javbus"
        assert K1 in state.failed
        assert K2 in state.failed
        assert K3 not in state.failed

    @pytest.mark.asyncio
    async def test_per_field_priority_overrides(self):
        """不同字段不同优先级: title 偏 javbus, studio 偏 dmm.

        title: [javbus, javdb, dmm]
        studio: [dmm, javdb, javbus]
        其余: [javdb, dmm, javbus]

        所有站点返回完整数据 → title 来自 javbus, studio 来自 dmm.
        """
        fp = defaultdict(
            lambda: [DB, DMM, BUS],
            {
                MetadataField.TITLE: [BUS, DB, DMM],
                MetadataField.STUDIO: [DMM, DB, BUS],
            },
        )
        graph = build_graph(fp, {})

        c_javdb = MockCrawler(result=MediaMetadata(number="X", title="T_DB", studio="S_DB"))
        c_dmm = MockCrawler(result=MediaMetadata(number="X", title="T_DMM", studio="S_DMM"))
        c_bus = MockCrawler(result=MediaMetadata(number="X", title="T_BUS", studio="S_BUS"))

        state = await execute_graph(graph, {K1: c_javdb, K2: c_dmm, K3: c_bus}, SearchQuery("X"))

        assert state.result.title == "T_BUS"  # javbus 第一优先
        assert state.result.field_sources["title"] == "javbus"
        assert state.result.studio == "S_DMM"  # dmm 第一优先
        assert state.result.field_sources["studio"] == "dmm"


class TestAggregateFieldOrder:
    """出演/评分把低优先级图片站拉进前波时, 列表仍按该字段站点顺序."""

    @pytest.mark.asyncio
    async def test_resrape_plugin_wave0_javdb_cache_later(self):
        """有码补刮: 插件因出演/评分在第 0 波, javdb 缓存后到.

        路由 dmm → javdb → 插件. actors/score 把插件提前.
        dmm 无数据; javdb 无海报、有封面与剧照; 插件三类都有.
        """
        fp = compile_priority(
            [DMM, DB, PLUGIN],
            {
                MetadataField.ACTORS: [PLUGIN, DB, DMM],
                MetadataField.SCORE: [PLUGIN, DB, DMM],
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
                PLUGIN: MockCrawler(plugin_meta),
            },
            SearchQuery("NFDM-311"),
            db_cache={"javdb": javdb_snap},
        )

        assert state.result.title == "FromJavDB"
        assert state.result.field_sources["title"] == "javdb"
        assert [a.name for a in state.result.actors] == ["P1"]
        assert state.result.field_sources["actors"] == PLUGIN
        assert state.result.poster_urls == ["http://plugin/poster.jpg"]
        assert state.result.thumb_urls == ["http://jdbstatic/cover.jpg", "http://plugin/thumb.jpg"]
        assert list(state.result.extrafanart_urls) == ["javdb", PLUGIN]
        assert [s.site for s in state.result.scores] == [PLUGIN, "javdb"]
        assert [s.score for s in state.result.scores] == [4.1, 4.36]

    @pytest.mark.asyncio
    async def test_thumb_prefer_then_route_remainder(self):
        """封面例外名单不含 javdb 时, 编译链仍是例外 ∩ 路由 + 其余路由保序."""
        fp = compile_priority(
            [DMM, DB, PLUGIN],
            {
                MetadataField.ACTORS: [PLUGIN, DB, DMM],
                MetadataField.THUMB_URLS: [DMM],
            },
        )
        graph = build_graph(fp, {})

        state = await execute_graph(
            graph,
            {
                "dmm": MockCrawler(None),
                K1: MockCrawler(
                    MediaMetadata(
                        number="X",
                        title="T",
                        thumb_urls=["http://javdb/t.jpg"],
                        extrafanart=["http://javdb/e.jpg"],
                    )
                ),
                PLUGIN: MockCrawler(
                    MediaMetadata.model_validate(
                        {
                            "number": "X",
                            "actors": ["A"],
                            "thumb_urls": ["http://plugin/t.jpg"],
                            "extrafanart": ["http://plugin/e.jpg"],
                        }
                    )
                ),
            },
            SearchQuery("X"),
        )

        assert state.result.thumb_urls == ["http://javdb/t.jpg", "http://plugin/t.jpg"]
        assert list(state.result.extrafanart_urls) == ["javdb", PLUGIN]

    @pytest.mark.asyncio
    async def test_higher_priority_empty_url_does_not_occupy_slot(self):
        """图片链上更前的站该字段为空时, 列表从下一站有值处开始, 不留空位."""
        fp = compile_priority([DB, DMM, PLUGIN], {MetadataField.SCORE: [PLUGIN, DB, DMM]})
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
                PLUGIN: MockCrawler(
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
        assert [s.site for s in state.result.scores] == [PLUGIN, "javdb"]


# ============================================================
# execute_graph / aggregate - 进度回调
# ============================================================


class TestProgressReporting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "desc, setup, expect_calls_min, expect_final",
        [
            ("完整元数据: 最终 current==标量字段数", "full", 1, "all"),
            ("全部失败: 仍上报但 current 为 0", "all_fail", 1, "zero"),
            ("无回调: 不报错且无事件", "full_no_cb", 0, "none"),
        ],
        ids=["full", "all_fail", "no_callback"],
    )
    async def test_execute_graph_progress(self, desc: str, setup: str, expect_calls_min: int, expect_final: str):
        fp = defaultdict(lambda: [DB, DMM])
        graph = build_graph(fp, {})
        events: list[tuple[int, int, str]] = []

        async def on_progress(current: int, total: int, message: str = "") -> None:
            events.append((current, total, message))

        field_total = len(SCALAR_FIELDS)
        cb: ProgressCallback | None
        if setup == "full":
            data = _full_metadata(number="X")
            crawlers = {K1: MockCrawler(data), K2: MockCrawler(data)}
            cb = on_progress
        elif setup == "all_fail":
            crawlers = {K1: MockCrawler(result=None), K2: FailingCrawler()}
            cb = on_progress
        elif setup == "full_no_cb":
            data = _full_metadata(number="X")
            crawlers = {K1: MockCrawler(data), K2: MockCrawler(data)}
            cb = None
        else:
            raise AssertionError(setup)

        await execute_graph(graph, crawlers, SearchQuery("X"), on_progress=cb)

        assert len(events) >= expect_calls_min, desc
        if expect_final == "none":
            assert events == []
            return

        assert all(t == field_total for _, t, _ in events), desc
        currents = [c for c, _, _ in events]
        assert currents == sorted(currents), f"not monotonic: {currents}"
        assert all(m for _, _, m in events), "message should name fetched sites"
        if expect_final == "all":
            assert events[-1][0] == field_total, desc
        elif expect_final == "zero":
            assert events[-1][0] == 0, desc
        else:
            raise AssertionError(expect_final)

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
# 集成测试: DAG 图 + ScrapeHandler
# ============================================================


class TestFetchGraphIntegration:
    @pytest.mark.asyncio
    async def test_aggregate_with_cache_and_fallback(self):
        """aggregate 配合 cache: javdb 快照命中, dmm 失败 → 部分成功."""
        fp = defaultdict(lambda: [DB, DMM])

        c_dmm = MockCrawler(result=None)  # dmm 失败

        result = await aggregate(
            SearchQuery("X"),
            {K1: MockCrawler(result=_full_metadata(number="X")), K2: c_dmm},
            fp,
            cache={"javdb": _full_metadata(number="X", title="CacheHit", studio="CS").model_dump()},
        )

        # javdb 缓存命中
        assert result.metadata.title == "CacheHit"
        assert result.metadata.studio == "CS"
        # dmm 失败被记录
        assert "dmm" in result.failed_sites
        # javdb 未被实际请求
        assert len(c_dmm.fetch_calls) == 1  # 只有 dmm 被真正请求
