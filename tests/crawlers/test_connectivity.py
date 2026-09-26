"""来源连通性探测: 判定表, 编排与插件回退."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from amane.config import HotSettings, PluginConfig, SiteConfig
from amane.crawlers.actor.base import ActorCrawler
from amane.crawlers.actor.registry import actor_registry
from amane.crawlers.actor.sites.gfriends import GFriendsActorCrawler
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.connectivity import ConnectivityChecker, SourceKind
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.registry import registry
from amane.crawlers.sites.official import Manufacturer, OfficialCrawler
from amane.crawlers.sites.prestige import _PROBE_SKU, PrestigeCrawler
from amane.crawlers.sites.theporndb import ThePornDBCrawler
from amane.net.connectivity import ConnectivityOutcome, ConnectivityStatus, SkipReason, probe_get
from amane.net.errors import FailureKind, FailureReason, RequestError, RequestFailure
from amane.plugin import (
    FilmSourcePlugin,
    FilmSourceProvider,
    PluginContext,
    SourceCapability,
    SourceDescriptor,
)
from amane.plugins.manager import PluginManager

if TYPE_CHECKING:
    from collections.abc import Mapping

    from amane.crawlers.models import FetchOptions, MediaMetadata, SearchQuery
    from amane.enums import SiteName
    from amane.net.http import WebClient


_URL = "https://probe.example.test/"


class _Resp:
    """WebClient 响应的最小替身: 探测只读 ``text`` / ``status_code``, GraphQL 探测另读 ``json()``."""

    def __init__(
        self,
        text: str,
        status: int = 200,
        *,
        unreadable: bool = False,
        payload: object | None = None,
    ) -> None:
        self._text = text
        self._unreadable = unreadable
        self._payload = payload
        self.status_code = status
        self.url = _URL
        self.headers: dict[str, str] = {}

    @property
    def text(self) -> str:
        if self._unreadable:
            raise RuntimeError("undecodable body")
        return self._text

    def json(self) -> object:
        return self._payload


class _FakeWeb:
    """按 URL 编程的假 WebClient: 记录调用参数, 失败用 ``RequestError`` 表达.

    ``default`` 给未编程的地址一个统一应答, 供「只要不落在某个地址上就算通过」的断言使用.
    """

    def __init__(
        self,
        responses: Mapping[str, _Resp | Exception],
        default: _Resp | Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self._responses = dict(responses)
        self._default = default

    async def request(self, method: str, url: str, **kwargs: object) -> _Resp:
        self.calls.append((method, url, kwargs))
        result = self._responses.get(url, self._default)
        if result is None:
            raise RequestError(url, RequestFailure(kind=FailureKind.CURL, message="no stub"))
        if isinstance(result, Exception):
            raise result
        return result

    async def get_json(self, url: str, **kwargs: object) -> object:
        """爬虫的 ``get_json`` 走同一套出站记录, 只是多一步解析."""
        return (await self.request("GET", url, **kwargs)).json()


class _FakeCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name="probe_film", base_url=_URL, cookies={"a": "b"})

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None


class _FakeActorCrawler(ActorCrawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name="probe_actor", base_url=_URL)

    async def _search(self, name: str) -> str | None:
        return None

    async def _scrape(self, url: str) -> MediaMetadata | None:
        return None


class _RaisingCrawler(_FakeCrawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name="probe_raising", base_url=_URL)

    async def check_connectivity(self) -> ConnectivityOutcome:
        raise RuntimeError("boom")


class _BrokenCrawler(_FakeCrawler):
    """构造即失败: 插件存盘配置与 config_model 不再匹配时就是这个形态."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name="probe_broken", base_url=_URL)

    def __init__(self, client: HttpClient, config: SiteConfig | None = None) -> None:
        raise RuntimeError("stored config no longer matches")


class _UndeclaredCrawler(_FakeCrawler):
    """声明不探测: 与插件 provider 返回 ``None`` 同一语义."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name="probe_undeclared", base_url=_URL)

    async def check_connectivity(self) -> ConnectivityOutcome | None:
        return None


class _EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Provider(FilmSourceProvider):
    def __init__(self, outcome: ConnectivityOutcome | None) -> None:
        self._outcome = outcome

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None

    async def check_connectivity(self) -> ConnectivityOutcome | None:
        return self._outcome


class _Plugin(FilmSourcePlugin):
    config_model = _EmptyConfig
    outcome: ConnectivityOutcome | None = None

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="acme.probe",
            name="Probe plugin",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            urls=("https://plugin.example.test/entry",),
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        return _Provider(type(self).outcome)


@pytest.fixture(autouse=True)
def _fake_crawlers():
    for cls in (_FakeCrawler, _RaisingCrawler, _BrokenCrawler, _UndeclaredCrawler):
        registry.register(cls)
    actor_registry.register(_FakeActorCrawler)
    yield
    for name in ("probe_film", "probe_actor", "probe_raising", "probe_broken", "probe_undeclared"):
        registry._crawlers.pop(name, None)
    actor_registry._classes.pop("probe_actor", None)


def _factory(web: _FakeWeb) -> CrawlerFactory:
    return CrawlerFactory(HttpClient(web=cast("WebClient", web)))


def _hot(
    *,
    routes: list[str],
    profile_sites: list[SiteName] | None = None,
    image_sites: list[SiteName] | None = None,
) -> HotSettings:
    hot = HotSettings()
    for chain in hot.scraping.content_routes.values():
        chain.clear()
    hot.scraping.content_routes[next(iter(hot.scraping.content_routes))] = list(routes)
    hot.actor_scraping.profile_sites = list(profile_sites or [])
    hot.actor_scraping.image_sites = list(image_sites or [])
    return hot


# 探测层的判定: 只钉「2xx 也要看正文」与「正文读不出来按空响应」两处 probe 自己的逻辑,
# 正文模式与状态码的分类规则见 test_base.py.
_PROBE_CASES: list[tuple[str, bool, ConnectivityStatus, FailureReason | None]] = [
    ("<html><body>hello</body></html>", False, ConnectivityStatus.OK, None),
    ("<div id='driver-verify'></div>", False, ConnectivityStatus.FAILED, FailureReason.AGE_VERIFICATION),
    ("", True, ConnectivityStatus.FAILED, FailureReason.EMPTY_RESPONSE),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("body", "unreadable", "status", "reason"), _PROBE_CASES)
async def test_probe_get_outcome(
    body: str, unreadable: bool, status: ConnectivityStatus, reason: FailureReason | None
) -> None:
    web = _FakeWeb({_URL: _Resp(body, unreadable=unreadable)})

    outcome = await probe_get(cast("WebClient", web), _URL)

    assert (outcome.status, outcome.reason) == (status, reason)
    assert (outcome.url, outcome.http_status) == (_URL, 200)
    # 探测是单次尝试: 重试只会把同一个结论拖长.
    assert [call[2]["max_attempts"] for call in web.calls] == [1]


# 探测只透传 RequestError 上的原因与状态码; 原因映射本身见 tests/net/test_errors.py.
_FAILURE_CASES: list[tuple[RequestFailure, FailureReason, int | None]] = [
    (RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404"), FailureReason.NOT_FOUND, 404),
    (RequestFailure(kind=FailureKind.CURL, message="curl error"), FailureReason.NETWORK, None),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("failure", "reason", "http_status"), _FAILURE_CASES)
async def test_probe_get_forwards_request_failure(
    failure: RequestFailure, reason: FailureReason, http_status: int | None
) -> None:
    web = _FakeWeb({_URL: RequestError(_URL, failure)})

    outcome = await probe_get(cast("WebClient", web), _URL)

    assert (outcome.status, outcome.reason, outcome.http_status) == (ConnectivityStatus.FAILED, reason, http_status)


@pytest.mark.asyncio(loop_scope="function")
async def test_default_probe_targets_base_url_with_cookies() -> None:
    """缺省探测点: GET ``base_url``, 带上合并后的 cookies."""
    web = _FakeWeb({_URL: _Resp("<html>ok</html>")})
    crawler = _FakeCrawler(HttpClient(web=cast("WebClient", web)))

    outcome = await crawler.check_connectivity()

    assert outcome.status is ConnectivityStatus.OK
    assert [(call[1], call[2]["cookies"]) for call in web.calls] == [(_URL, {"a": "b"})]


# ``source_ids`` 为 None 时取当前热配置会真正请求的来源, 显式传入时只探这些 (界面单点重试).
_SCOPE_CASES: list[tuple[list[str] | None, list[str], list[SourceKind]]] = [
    (None, ["probe_film", "probe_actor"], [SourceKind.FILM, SourceKind.ACTOR]),
    (["probe_actor"], ["probe_actor"], [SourceKind.ACTOR]),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("source_ids", "expected_ids", "expected_kinds"), _SCOPE_CASES)
async def test_checker_scope(
    source_ids: list[str] | None, expected_ids: list[str], expected_kinds: list[SourceKind]
) -> None:
    web = _FakeWeb({_URL: _Resp("<html>ok</html>")})
    hot = _hot(routes=["probe_film", "probe_actor", "probe_film"], profile_sites=[cast("SiteName", "probe_actor")])
    checker = ConnectivityChecker(_factory(web), hot)

    checks = await checker.check(source_ids)

    assert [check.source_id for check in checks] == expected_ids
    assert [check.kind for check in checks] == expected_kinds
    assert {check.outcome.status for check in checks} == {ConnectivityStatus.OK}


_RECOVERY_CASES: list[tuple[str, ConnectivityStatus, FailureReason | None, SkipReason | None, str | None, bool]] = [
    # 来源不存在: 报 skipped 并给出结构化原因, 不计入失败.
    ("nope", ConnectivityStatus.SKIPPED, None, SkipReason.UNKNOWN_SOURCE, None, False),
    # 来源自己抛异常: 结果里只写类型名, 全文只写入日志; 探测发生过, 因此有耗时.
    ("probe_raising", ConnectivityStatus.FAILED, FailureReason.UNEXPECTED, None, "RuntimeError", True),
    # 取来源实例失败 (插件配置坏 / 插件升级): 同样只让这一个来源不可用, 没有探测就没有耗时.
    ("probe_broken", ConnectivityStatus.FAILED, FailureReason.CRAWLER_UNAVAILABLE, None, "RuntimeError", False),
    # 来源声明不探测: 与失败区分, 原因走 skip_reason.
    ("probe_undeclared", ConnectivityStatus.SKIPPED, None, SkipReason.UNDECLARED, None, False),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("source_id", "status", "reason", "skip_reason", "detail", "probed"), _RECOVERY_CASES)
async def test_checker_reports_unknown_and_unexpected(
    source_id: str,
    status: ConnectivityStatus,
    reason: FailureReason | None,
    skip_reason: SkipReason | None,
    detail: str | None,
    probed: bool,
) -> None:
    checker = ConnectivityChecker(_factory(_FakeWeb({})), _hot(routes=["probe_film"]))

    (check,) = await checker.check([source_id])

    assert (check.outcome.status, check.outcome.reason, check.outcome.skip_reason, check.outcome.detail) == (
        status,
        reason,
        skip_reason,
        detail,
    )
    # 耗时只在探测发生过时给出: 界面把 null 显示为「—」, 与「无法探测」一致.
    assert (check.elapsed_ms is not None) is probed


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("outcome", "expected_url", "expected_calls"),
    [
        # 未声明探测方式: 主机按 descriptor 的首个 URL 探.
        (None, "https://plugin.example.test/entry", ["https://plugin.example.test/entry"]),
        # 插件自己给了结论: 主机不再发请求.
        (ConnectivityOutcome.ok("https://own.example.test/", 200), "https://own.example.test/", []),
    ],
)
@pytest.mark.asyncio(loop_scope="function")
async def test_checker_plugin_probe_and_fallback(
    tmp_path,
    outcome: ConnectivityOutcome | None,
    expected_url: str,
    expected_calls: list[str],
) -> None:
    _Plugin.outcome = outcome
    web = _FakeWeb(
        {
            "https://plugin.example.test/entry": _Resp("<html>ok</html>"),
            "https://own.example.test/": _Resp("<html>ok</html>"),
        }
    )
    manager = PluginManager({"acme.probe": _Plugin()}, [])
    factory = CrawlerFactory(
        HttpClient(web=cast("WebClient", web)),
        data_dir=tmp_path,
        plugin_manager=manager,
        plugin_configs={"acme.probe": PluginConfig()},
    )

    (check,) = await ConnectivityChecker(factory, _hot(routes=["acme.probe"]), manager).check()

    assert (check.kind, check.name) == (SourceKind.PLUGIN, "Probe plugin")
    assert check.outcome.status is ConnectivityStatus.OK
    assert check.outcome.url == expected_url
    assert [call[1] for call in web.calls] == expected_calls


# 覆盖缺省探测的来源: 探测入口与 ``base_url`` 不同, 因此各自钉住「探测落在哪个地址」.
# 首页一律回年龄墙式的拦截页, 未编程的地址按 ``default`` 应答: 探测落回 ``base_url`` 就会失败.


@pytest.mark.asyncio(loop_scope="function")
async def test_prestige_probe_targets_the_scrape_entry() -> None:
    """探测与刮削取同一个入口: 探测落回带年龄墙的首页会得到 age_verification."""
    base = PrestigeCrawler.profile().base_url
    sku = {"parentProduct": {"uuid": "bdcdcfc9-f375-431a-80c5-87b688f1548a"}}
    web = _FakeWeb({base: _Resp("<div id='driver-verify'></div>")}, default=_Resp('{"uuid":"a55b0c4b"}', payload=sku))
    crawler = PrestigeCrawler(HttpClient(web=cast("WebClient", web)))

    # 先看刮削路径 (同一个番号) 请求了哪个地址, 再要求探测落在同一处.
    await crawler._search(SearchQuery(number=_PROBE_SKU))
    scraped_url = web.calls[0][1]

    outcome = await crawler.check_connectivity()

    assert (outcome.status, outcome.url) == (ConnectivityStatus.OK, scraped_url)
    assert [call[1] for call in web.calls] == [scraped_url, scraped_url]


_ThePornDBCases: list[
    tuple[
        str | None,
        dict[str, object] | None,
        ConnectivityStatus,
        FailureReason | None,
        SkipReason | None,
        str | None,
        int | None,
    ]
] = [
    # 没配 token: 不探测, 也不计入失败; 说明里只写缺少的配置项名 (界面原样渲染, 不翻译).
    (None, None, ConnectivityStatus.SKIPPED, None, SkipReason.MISSING_CREDENTIAL, "api_token", None),
    # GraphQL 层报错: 状态码 200 不是失败原因 (因此不给 http_status), 原因由枚举表达, 上游的错误文本
    # 作为可行动信息进 detail.
    (
        "token",
        {"errors": [{"message": "boom"}]},
        ConnectivityStatus.FAILED,
        FailureReason.API_ERROR,
        None,
        "boom",
        None,
    ),
    # 正常应答: 结论落在探测地址上.
    ("token", {"data": None}, ConnectivityStatus.OK, None, None, None, 200),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("token", "payload", "status", "reason", "skip_reason", "detail", "http_status"), _ThePornDBCases
)
async def test_theporndb_probe_states(
    token: str | None,
    payload: dict[str, object] | None,
    status: ConnectivityStatus,
    reason: FailureReason | None,
    skip_reason: SkipReason | None,
    detail: str | None,
    http_status: int | None,
) -> None:
    base = ThePornDBCrawler.profile().base_url
    web = _FakeWeb({}, default=_Resp("{}", payload=payload))
    crawler = ThePornDBCrawler(HttpClient(web=cast("WebClient", web)), config=SiteConfig(api_token=token))

    outcome = await crawler.check_connectivity()

    assert (outcome.status, outcome.reason, outcome.skip_reason, outcome.detail) == (
        status,
        reason,
        skip_reason,
        detail,
    )
    assert (outcome.url, outcome.http_status) == (base if token else None, http_status)
    assert [call[1] for call in web.calls] == ([base] if token else [])


_OfficialCases: list[tuple[dict[str, Manufacturer], str]] = [
    # 没有用户路由: 取默认表首项.
    ({}, "attackers.net"),
    # 配了路由: 探用户真正会请求的域, 而不是默认表首项.
    ({"HONNAKA": Manufacturer.HONNAKA}, "honnaka.jp"),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("routes", "expected_host"), _OfficialCases)
async def test_official_probe_follows_configured_routes(routes: dict[str, Manufacturer], expected_host: str) -> None:
    web = _FakeWeb({}, default=_Resp("<html>ok</html>"))
    crawler = OfficialCrawler(HttpClient(web=cast("WebClient", web)), config=SiteConfig(official_routes=routes))

    outcome = await crawler.check_connectivity()

    assert outcome.status is ConnectivityStatus.OK
    assert [call[1] for call in web.calls] == [f"https://{expected_host}"]


_VALUE_STRINGS: list[tuple[dict[str, object], ConnectivityStatus, SkipReason | None]] = [
    # 值字符串是 API JSON 里的形态: 从响应体抄写或用字面量的插件不该得到「意外错误」.
    (
        {"status": "skipped", "skip_reason": "missing_credential"},
        ConnectivityStatus.SKIPPED,
        SkipReason.MISSING_CREDENTIAL,
    ),
    ({"status": "ok"}, ConnectivityStatus.OK, None),
]


@pytest.mark.parametrize(("raw", "status", "skip_reason"), _VALUE_STRINGS)
def test_outcome_accepts_value_strings(
    raw: dict[str, object], status: ConnectivityStatus, skip_reason: SkipReason | None
) -> None:
    outcome = ConnectivityOutcome(**raw)  # type: ignore[arg-type]

    assert (outcome.status, outcome.skip_reason) == (status, skip_reason)


_INVALID_OUTCOMES: list[tuple[dict[str, object]]] = [
    # 拼错的取值与跨枚举误配仍被拒, 且发生在来源自己的调用里 (只使该来源报 unexpected).
    ({"status": "skipped", "skip_reason": "拼错的"},),
    ({"status": "failed", "reason": "http_error2"},),
    ({"status": 1},),
    # 结论与原因字段的对应关系同样是契约: 失败必须有 reason, 未探测必须有 skip_reason.
    ({"status": ConnectivityStatus.FAILED},),
    ({"status": ConnectivityStatus.OK, "reason": FailureReason.NETWORK},),
    ({"status": ConnectivityStatus.SKIPPED},),
]


@pytest.mark.parametrize(("kwargs",), _INVALID_OUTCOMES)
def test_outcome_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConnectivityOutcome(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio(loop_scope="function")
async def test_gfriends_probe_targets_the_tree_that_scraping_fetches() -> None:
    """仓库目录本身 404: 探测必须落在刮削真正取的 ``Filetree.json`` 上."""
    web = _FakeWeb({}, default=_Resp('{"Content":{}}', payload={}))
    crawler = GFriendsActorCrawler(HttpClient(web=cast("WebClient", web)))

    await crawler._ensure_index()
    fetched_url = web.calls[0][1]

    outcome = await crawler.check_connectivity()

    assert (outcome.status, outcome.url) == (ConnectivityStatus.OK, fetched_url)
    assert [call[1] for call in web.calls] == [fetched_url, fetched_url]
