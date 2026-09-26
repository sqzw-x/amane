"""HTTP 限速器缓存 / 覆盖; RequestError 状态分类; 同源 Referer 注入; 重试次数."""

import asyncio
from typing import Any, ClassVar

import pytest

from amane.net.errors import FailureKind, FailureReason, RequestError, RequestFailure
from amane.net.http import RateLimiters, WebClient, _with_same_origin_referer


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
    """替换 ``WebClient._session``: 记录出站参数, 不发请求."""

    def __init__(self, response: _StubResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or _StubResponse()

    async def request(self, method: str, url: str, **kwargs: Any) -> _StubResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response


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
    """重试次数: ``max_attempts`` 向下覆盖配置值, 但两者都至少发一次请求."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("max_retries", "max_attempts", "status", "expected_calls", "raises"),
        [
            # 未覆盖: 按配置的重试次数.
            (3, None, 200, 1, False),
            (3, None, 503, 3, True),
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
