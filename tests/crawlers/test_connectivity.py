"""来源连通性探测: 判定表, 编排与插件回退."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel, ConfigDict

from amane.config import HotSettings, PluginConfig
from amane.crawlers.actor.base import ActorCrawler
from amane.crawlers.actor.registry import actor_registry
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.connectivity import ConnectivityChecker, SourceKind
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.http import HttpClient
from amane.crawlers.registry import registry
from amane.net.connectivity import ConnectivityOutcome, ConnectivityStatus, probe_get
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
    """WebClient 响应的最小替身: 探测只读 ``text`` / ``status_code``."""

    def __init__(self, text: str, status: int = 200, *, unreadable: bool = False) -> None:
        self._text = text
        self._unreadable = unreadable
        self.status_code = status
        self.url = _URL
        self.headers: dict[str, str] = {}

    @property
    def text(self) -> str:
        if self._unreadable:
            raise RuntimeError("undecodable body")
        return self._text


class _FakeWeb:
    """按 URL 编程的假 WebClient: 记录调用参数, 失败用 ``RequestError`` 表达."""

    def __init__(self, responses: Mapping[str, _Resp | Exception]) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self._responses = dict(responses)

    async def request(self, method: str, url: str, **kwargs: object) -> _Resp:
        self.calls.append((method, url, kwargs))
        result = self._responses.get(url)
        if result is None:
            raise RequestError(url, RequestFailure(kind=FailureKind.CURL, message="no stub"))
        if isinstance(result, Exception):
            raise result
        return result


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
    for cls in (_FakeCrawler, _RaisingCrawler):
        registry.register(cls)
    actor_registry.register(_FakeActorCrawler)
    yield
    for name in ("probe_film", "probe_actor", "probe_raising"):
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


_RECOVERY_CASES: list[tuple[str, ConnectivityStatus, FailureReason | None, str]] = [
    # 来源不存在: 报 skipped, 不计入失败.
    ("nope", ConnectivityStatus.SKIPPED, None, "来源不存在或未启用"),
    # 来源自己抛异常: 结果里只写类型名, 全文只进日志.
    ("probe_raising", ConnectivityStatus.FAILED, FailureReason.UNEXPECTED, "RuntimeError"),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("source_id", "status", "reason", "detail"), _RECOVERY_CASES)
async def test_checker_reports_unknown_and_unexpected(
    source_id: str, status: ConnectivityStatus, reason: FailureReason | None, detail: str
) -> None:
    checker = ConnectivityChecker(_factory(_FakeWeb({})), _hot(routes=["probe_film"]))

    (check,) = await checker.check([source_id])

    assert (check.outcome.status, check.outcome.reason, check.outcome.detail) == (status, reason, detail)


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
