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

    def __init__(self, text: str, status: int = 200, url: str = _URL) -> None:
        self._text = text
        self.status_code = status
        self.url = url
        self.headers: dict[str, str] = {}

    @property
    def text(self) -> str:
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


_BODY_CASES: list[tuple[str, ConnectivityStatus, FailureReason | None]] = [
    ("<html><body>hello</body></html>", ConnectivityStatus.OK, None),
    ("This content is not available in your region.", ConnectivityStatus.FAILED, FailureReason.GEO_RESTRICTED),
    ("<title>Just a moment...</title> cloudflare", ConnectivityStatus.FAILED, FailureReason.CLOUDFLARE_CHALLENGE),
    ("<div class='cf-error'>Ray-ID: 8f2a</div>", ConnectivityStatus.FAILED, FailureReason.CLOUDFLARE_BLOCKED),
    ("<div id='driver-verify'></div>", ConnectivityStatus.FAILED, FailureReason.AGE_VERIFICATION),
    ("", ConnectivityStatus.FAILED, FailureReason.EMPTY_RESPONSE),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("body", "status", "reason"), _BODY_CASES)
async def test_probe_get_classifies_body(body: str, status: ConnectivityStatus, reason: FailureReason | None) -> None:
    web = _FakeWeb({_URL: _Resp(body)})

    outcome = await probe_get(cast("WebClient", web), _URL)

    assert (outcome.status, outcome.reason) == (status, reason)
    assert outcome.url == _URL
    assert outcome.http_status == 200
    assert web.calls == [("GET", _URL, {"cookies": None, "headers": None, "timeout": None, "max_attempts": 1})]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (RequestFailure(kind=FailureKind.TIMEOUT, message="timeout"), FailureReason.TIMEOUT),
        (RequestFailure(kind=FailureKind.CURL, message="curl error"), FailureReason.NETWORK),
        (
            RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404"),
            FailureReason.NOT_FOUND,
        ),
        (
            RequestFailure(
                kind=FailureKind.HTTP_STATUS,
                status=403,
                message="HTTP 403",
                body=b"banned your access",
            ),
            FailureReason.IP_BANNED,
        ),
    ],
)
async def test_probe_get_maps_request_failure(failure: RequestFailure, reason: FailureReason) -> None:
    web = _FakeWeb({_URL: RequestError(_URL, failure)})

    outcome = await probe_get(cast("WebClient", web), _URL)

    assert (outcome.status, outcome.reason) == (ConnectivityStatus.FAILED, reason)
    assert outcome.http_status == failure.status


@pytest.mark.asyncio(loop_scope="function")
async def test_checker_uses_configured_sources_and_dedupes() -> None:
    web = _FakeWeb({_URL: _Resp("<html>ok</html>")})
    hot = _hot(routes=["probe_film", "probe_actor", "probe_film"], profile_sites=[cast("SiteName", "probe_actor")])
    checker = ConnectivityChecker(_factory(web), hot)

    checks = await checker.check()

    assert [check.source_id for check in checks] == ["probe_film", "probe_actor"]
    assert [(check.kind, check.outcome.status) for check in checks] == [
        (SourceKind.FILM, ConnectivityStatus.OK),
        (SourceKind.ACTOR, ConnectivityStatus.OK),
    ]
    assert web.calls[0][2]["cookies"] == {"a": "b"}


@pytest.mark.asyncio(loop_scope="function")
async def test_checker_explicit_ids_only() -> None:
    web = _FakeWeb({_URL: _Resp("<html>ok</html>")})
    checker = ConnectivityChecker(_factory(web), _hot(routes=["probe_film"]))

    checks = await checker.check(["probe_actor"])

    assert [check.source_id for check in checks] == ["probe_actor"]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("source_id", "detail"),
    [
        ("nope", "来源不存在或未启用"),
        ("probe_raising", "RuntimeError"),
    ],
)
async def test_checker_reports_unknown_and_unexpected(source_id: str, detail: str) -> None:
    checker = ConnectivityChecker(_factory(_FakeWeb({})), _hot(routes=["probe_film"]))

    (check,) = await checker.check([source_id])

    assert check.outcome.status is ConnectivityStatus.FAILED or check.outcome.status is ConnectivityStatus.SKIPPED
    assert check.outcome.detail == detail
    assert check.outcome.reason in (None, FailureReason.UNEXPECTED)


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
