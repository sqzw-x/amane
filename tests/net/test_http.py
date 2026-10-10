"""HTTP 限速器缓存 / 覆盖; RequestError 状态分类; 同源 Referer 注入; 重试次数; 指纹轮换; 站点代理."""

import asyncio
import random
from typing import Any, ClassVar

import pytest

from amane.config import SiteConfig
from amane.net.errors import FailureKind, FailureReason, RequestError, RequestFailure
from amane.net.http import (
    _IMPERSONATE_OPTIONS,
    _MAX_IMPERSONATE_ROTATIONS,
    RateLimiters,
    WebClient,
    _FingerprintPicker,
    _with_same_origin_referer,
    site_proxy_overrides,
)


class TestRequestError:
    def test_classifies_http_status(self):
        err = RequestError(
            "https://example.com", RequestFailure(kind=FailureKind.HTTP_STATUS, status=429, message="HTTP 429")
        )
        assert err.reason == FailureReason.RATE_LIMITED
        assert err.http_status == 429


class TestRateLimiters:
    def test_returns_cached_limiter(self):
        rl = RateLimiters(default_rate=5)
        assert rl.get("example.com") is rl.get("example.com")

    def test_different_hosts(self):
        rl = RateLimiters(default_rate=5)
        assert rl.get("a.com") is not rl.get("b.com")

    def test_custom_rate_sets_period(self):
        rl = RateLimiters(default_rate=5)
        limiter = rl.get("custom.com", rate=42.0)
        assert limiter.time_period == pytest.approx(1 / 42.0)

    def test_localhost_uses_high_rate(self):
        rl = RateLimiters(default_rate=5)
        assert rl.get("localhost").time_period == pytest.approx(1 / 300.0)
        assert rl.get("example.com").time_period == pytest.approx(1 / 5)

    def test_set_rate_replaces_limiter(self):
        rl = RateLimiters(default_rate=5)
        old = rl.get("example.com")
        rl.set_rate("example.com", 100.0)
        new = rl.get("example.com")
        assert old is not new
        assert new.time_period == pytest.approx(1 / 100.0)

    def test_from_config_network_rate_overrides(self):
        rl = RateLimiters.from_config({"api.example.com": 20.0}, {}, {}, default_rate=5)
        assert rl.get("api.example.com").time_period == pytest.approx(1 / 20.0)
        assert rl.get("other.com").time_period == pytest.approx(1 / 5)


_JAVBUS = frozenset({"www.javbus.com"})


class _StubResponse:
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, *, url: str = "", content: bytes = b"", status: int = 200) -> None:
        self.url = url
        self.content = content
        self.status_code = status


class _StubSession:
    """替换 ``WebClient._session``: 按调用次序返回应答 (用尽后重复最后一个), 记录出站参数."""

    def __init__(self, *responses: _StubResponse) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses) or [_StubResponse()]

    async def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class TestSameOriginReferer:
    @pytest.mark.parametrize(
        ("host", "headers", "expected"),
        [
            ("www.javbus.com", None, {"Referer": "https://www.javbus.com/"}),
            (
                "www.javbus.com",
                {"Accept-Language": "zh-CN"},
                {"Accept-Language": "zh-CN", "Referer": "https://www.javbus.com/"},
            ),
            # 调用方已给 Referer 时保持原值, 大小写不敏感
            ("www.javbus.com", {"referer": "https://other.example/"}, {"referer": "https://other.example/"}),
            ("example.com", None, None),
            (None, None, None),
        ],
    )
    def test_injects_only_on_declared_host_without_referer(
        self, host: str | None, headers: dict[str, str] | None, expected: dict[str, str] | None
    ):
        assert _with_same_origin_referer(host, headers, _JAVBUS) == expected

    def test_keeps_caller_headers_intact(self):
        headers = {"Accept-Language": "zh-CN"}
        result = _with_same_origin_referer("www.javbus.com", headers, _JAVBUS)
        assert headers == {"Accept-Language": "zh-CN"}
        assert result is not headers

    @pytest.mark.asyncio
    async def test_request_applies_injection(self, monkeypatch):
        client = WebClient(limiters=RateLimiters(default_rate=100), same_origin_referer_hosts=_JAVBUS)
        session = _StubSession()
        monkeypatch.setattr(client, "_session", session)

        await client.request("GET", "https://www.javbus.com/pics/cover/1.jpg")

        assert session.calls[0]["headers"] == {"Referer": "https://www.javbus.com/"}


class TestRequestAttempts:
    """重试次数: 配置值是首次请求之外的重试次数; ``max_attempts`` 是总次数上限, 向下覆盖."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("max_retries", "max_attempts", "status", "expected_calls", "raises"),
        [
            # 未覆盖: 按配置的重试次数 (首次请求之外), 因此最多 1 + 3 次.
            (3, None, 200, 1, False),
            (3, None, 503, 4, True),
            (2, None, 503, 3, True),
            # 配置 0 表示不重试, 不是一次都不发.
            (0, None, 200, 1, False),
            (0, None, 503, 1, True),
            # 探测的单次尝试: 覆盖配置里的重试次数.
            (3, 1, 503, 1, True),
            (3, 2, 503, 2, True),
        ],
    )
    async def test_attempts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        max_retries: int,
        max_attempts: int | None,
        status: int,
        expected_calls: int,
        raises: bool,
    ):
        async def _no_sleep(_seconds: float) -> None:
            return None

        client = WebClient(max_retries=max_retries, limiters=RateLimiters(default_rate=100))
        session = _StubSession(_StubResponse(status=status))
        monkeypatch.setattr(client, "_session", session)
        # 重试之间的等待与结论无关, 缩短测试墙钟.
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        if raises:
            with pytest.raises(RequestError):
                await client.request("GET", "https://example.com/x", max_attempts=max_attempts)
        else:
            await client.request("GET", "https://example.com/x", max_attempts=max_attempts)

        assert len(session.calls) == expected_calls


async def _no_sleep(_seconds: float) -> None:
    return None


def _blocked_client(monkeypatch: pytest.MonkeyPatch, session: _StubSession, **kwargs: Any) -> WebClient:
    """默认指纹固定为列表首项, 否则无法断言换到了哪一个."""
    monkeypatch.setattr(random, "choice", lambda options: options[0])
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    client = WebClient(limiters=RateLimiters(default_rate=100), **kwargs)
    monkeypatch.setattr(client, "_session", session)
    return client


class TestFingerprintPicker:
    def test_rotates_in_order_then_stops(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(random, "choice", lambda options: options[0])
        picker = _FingerprintPicker(_IMPERSONATE_OPTIONS)

        assert picker.select("a.com") == _IMPERSONATE_OPTIONS[0]
        assert [picker.rotate("a.com") for _ in _IMPERSONATE_OPTIONS] == [*_IMPERSONATE_OPTIONS[1:], None]
        # 试尽之后停在最后一个, 不再回到默认值
        assert picker.select("a.com") == _IMPERSONATE_OPTIONS[-1]

    def test_rotation_is_per_host(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(random, "choice", lambda options: options[0])
        picker = _FingerprintPicker(_IMPERSONATE_OPTIONS)

        assert picker.rotate("a.com") == _IMPERSONATE_OPTIONS[1]
        assert picker.select("b.com") == _IMPERSONATE_OPTIONS[0]


class TestImpersonateRotation:
    """403/406 判定为站点拦下此指纹: 换下一个未试过的指纹重发, 轮换不占重试预算."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "max_retries", "max_attempts", "expected_status"),
        [
            # 换过指纹就成功: 第二次请求用下一个指纹.
            (403, 2, None, 200),
            (406, 2, None, 200),
            # 资源不存在: 换指纹无意义, 只发一次.
            (404, 2, None, None),
            # 配置不重试 (max_retries=0) 与探测的单次尝试 (max_attempts=1) 都不阻止轮换.
            (403, 0, None, 200),
            (403, 0, 1, 200),
        ],
    )
    async def test_rotates_only_on_blocked_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
        max_retries: int,
        max_attempts: int | None,
        expected_status: int | None,
    ):
        session = _StubSession(_StubResponse(status=status), _StubResponse(status=200))
        client = _blocked_client(monkeypatch, session, max_retries=max_retries)

        if expected_status is None:
            with pytest.raises(RequestError):
                await client.request("GET", "https://pics.example/x.jpg", max_attempts=max_attempts)
        else:
            resp = await client.request("GET", "https://pics.example/x.jpg", max_attempts=max_attempts)
            assert resp.status_code == expected_status

        impersonated = [call["impersonate"] for call in session.calls]
        assert impersonated == list(_IMPERSONATE_OPTIONS[: len(session.calls)])

    @pytest.mark.asyncio
    async def test_rotated_fingerprint_is_reused_for_host(self, monkeypatch: pytest.MonkeyPatch):
        session = _StubSession(_StubResponse(status=403), _StubResponse(status=200), _StubResponse(status=200))
        client = _blocked_client(monkeypatch, session)
        url = "https://pics.example/x.jpg"

        assert (await client.request("GET", url)).status_code == 200
        assert (await client.request("GET", url)).status_code == 200

        # 第二个请求不再试探默认指纹
        assert [call["impersonate"] for call in session.calls] == [
            _IMPERSONATE_OPTIONS[0],
            _IMPERSONATE_OPTIONS[1],
            _IMPERSONATE_OPTIONS[1],
        ]

    @pytest.mark.asyncio
    async def test_other_host_keeps_process_default(self, monkeypatch: pytest.MonkeyPatch):
        session = _StubSession(_StubResponse(status=403), _StubResponse(status=200), _StubResponse(status=200))
        client = _blocked_client(monkeypatch, session)

        await client.request("GET", "https://a.example/x.jpg")
        await client.request("GET", "https://b.example/x.jpg")

        assert [call["impersonate"] for call in session.calls] == [
            _IMPERSONATE_OPTIONS[0],
            _IMPERSONATE_OPTIONS[1],
            _IMPERSONATE_OPTIONS[0],
        ]

    @pytest.mark.asyncio
    async def test_rotation_budget_caps_extra_requests(self, monkeypatch: pytest.MonkeyPatch):
        session = _StubSession(_StubResponse(status=403))
        client = _blocked_client(monkeypatch, session, max_retries=3)

        with pytest.raises(RequestError):
            await client.request("GET", "https://blocked.example/x.jpg")

        assert [call["impersonate"] for call in session.calls] == list(
            _IMPERSONATE_OPTIONS[: _MAX_IMPERSONATE_ROTATIONS + 1]
        )

    @pytest.mark.asyncio
    async def test_rejected_every_fingerprint_stops_rotating(self, monkeypatch: pytest.MonkeyPatch):
        """六个指纹全被拒后不再换: 第三次起每次调用只发出一次请求."""
        session = _StubSession(_StubResponse(status=403))
        client = _blocked_client(monkeypatch, session)
        url = "https://blocked.example/x.jpg"

        for _ in range(3):
            with pytest.raises(RequestError):
                await client.request("GET", url)

        # 首次用满预算 4 次, 第二次补齐剩余 2 个, 之后退回单次
        assert len(session.calls) == 4 + 2 + 1
        assert session.calls[-1]["impersonate"] == _IMPERSONATE_OPTIONS[-1]


_GLOBAL_PROXY = "http://127.0.0.1:7890"
_SITE_PROXY = "socks5://127.0.0.1:1080"


class TestSiteProxyOverrides:
    """站点级代理按 host 汇总, 与限速器取同一份 host 集合 (profile URL + 配置的 base_url)."""

    @pytest.mark.parametrize(
        ("config", "site_urls", "expected"),
        [
            # 站点代理: 该站点的每个 host 都映射到它, 配置的镜像域同样纳入
            (
                SiteConfig(proxy=_SITE_PROXY, base_url="https://mirror.example"),
                {"javdb": ["https://javdb.com", "https://javdb365.com"]},
                {"javdb.com": _SITE_PROXY, "javdb365.com": _SITE_PROXY, "mirror.example": _SITE_PROXY},
            ),
            # 站点关闭代理: 显式直连, 站点代理不再生效
            (SiteConfig(use_proxy=False, proxy=_SITE_PROXY), {"javdb": ["https://javdb.com"]}, {"javdb.com": None}),
            (SiteConfig(use_proxy=False), {"javdb": ["https://javdb.com"]}, {"javdb.com": None}),
            # 未配置站点代理: 该站点不进入结果, 沿用全局代理
            (SiteConfig(), {"javdb": ["https://javdb.com"]}, {}),
            # 站点没有已知 host: 不产生映射
            (SiteConfig(proxy=_SITE_PROXY), {}, {}),
        ],
    )
    def test_collects_overrides(self, config: SiteConfig, site_urls: dict[str, list[str]], expected: dict):
        assert site_proxy_overrides({"javdb": config}, site_urls) == expected

    def test_shared_host_keeps_last_site(self):
        """多个站点共享 host 时按遍历顺序最后一次写入生效 (与限速器同一口径)."""
        overrides = site_proxy_overrides(
            {"a": SiteConfig(proxy="http://127.0.0.1:1111"), "b": SiteConfig(proxy="http://127.0.0.1:2222")},
            {"a": ["https://shared.example"], "b": ["https://shared.example"]},
        )
        assert overrides == {"shared.example": "http://127.0.0.1:2222"}


class TestProxyResolution:
    """代理按 host 解析: 站点级覆盖优先于全局, 调用方 ``use_proxy=False`` 强制直连."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("url", "use_proxy", "expected"),
        [
            ("https://global.example/x", True, _GLOBAL_PROXY),
            ("https://site.example/x", True, _SITE_PROXY),
            # 站点显式关闭代理
            ("https://direct.example/x", True, None),
            # 本机服务 (solver) 强制直连, 站点代理也不生效
            ("https://site.example/x", False, None),
        ],
    )
    async def test_request_uses_host_proxy(
        self, monkeypatch: pytest.MonkeyPatch, url: str, use_proxy: bool, expected: str | None
    ):
        client = WebClient(
            proxy=_GLOBAL_PROXY,
            proxy_overrides={"site.example": _SITE_PROXY, "direct.example": None},
            limiters=RateLimiters(default_rate=100),
        )
        session = _StubSession()
        monkeypatch.setattr(client, "_session", session)

        await client.request("GET", url, use_proxy=use_proxy)

        assert session.calls[0]["proxy"] == expected

    @pytest.mark.asyncio
    async def test_download_follows_host_proxy(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """下载的探测与正文请求走同一代理."""
        client = WebClient(
            proxy=_GLOBAL_PROXY,
            proxy_overrides={"pics.example": _SITE_PROXY},
            limiters=RateLimiters(default_rate=100),
        )
        session = _StubSession(_StubResponse(content=b"jpeg-bytes"))
        monkeypatch.setattr(client, "_session", session)

        assert await client.download("https://pics.example/a.jpg", tmp_path / "a.jpg") is True

        assert {call["proxy"] for call in session.calls} == {_SITE_PROXY}
