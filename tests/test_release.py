"""GitHub latest 检查: 版本比较与 ETag 缓存."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx2 as httpx
import pytest

from amane.release import GITHUB_LATEST_URL, ReleaseChecker, is_newer


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v1.0.1", "1.0.0", True),
        ("1.0.1", "v1.0.0", True),
        ("v1.0.0", "1.0.0", False),
        ("v1.0.0", "1.0.1", False),
        ("not-a-version", "1.0.0", True),
        ("1.0.0", "1.0.0", False),
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert is_newer(latest, current) is expected


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None, etag: str | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = {"etag": etag} if etag else {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []
        self.urls: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.urls.append(url)
        self.calls.append(headers or {})
        return self.responses.pop(0)


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_parses_latest_and_caches() -> None:
    fake = _FakeClient([_FakeResponse(200, {"tag_name": "v1.2.0", "html_url": "https://example.com/r"}, etag='"abc"')])
    with patch("amane.release.httpx.AsyncClient", return_value=fake):
        checker = ReleaseChecker()
        first = await checker.fetch()
        second = await checker.fetch()
    assert first.latest == "v1.2.0"
    assert first.html_url == "https://example.com/r"
    assert second.latest == "v1.2.0"
    assert len(fake.calls) == 1
    assert fake.urls == [GITHUB_LATEST_URL]


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_uses_override_url() -> None:
    fake = _FakeClient([_FakeResponse(200, {"tag_name": "v9.9.9", "html_url": "https://example.com/r"})])
    override = "http://127.0.0.1:18765/releases/latest"
    with patch("amane.release.httpx.AsyncClient", return_value=fake):
        snap = await ReleaseChecker().fetch(url=override)
    assert snap.latest == "v9.9.9"
    assert fake.urls == [override]


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_sends_etag_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("amane.release._CACHE_TTL_S", 0.0)
    fake = _FakeClient(
        [
            _FakeResponse(200, {"tag_name": "v1.0.0", "html_url": "https://example.com/a"}, etag='"e1"'),
            _FakeResponse(304),
        ]
    )
    with patch("amane.release.httpx.AsyncClient", return_value=fake):
        checker = ReleaseChecker()
        await checker.fetch()
        snap = await checker.fetch()
    assert snap.latest == "v1.0.0"
    assert fake.calls[1].get("If-None-Match") == '"e1"'


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_http_error_returns_empty() -> None:
    class _Boom(_FakeClient):
        async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            raise httpx.ConnectError("down")

    with patch("amane.release.httpx.AsyncClient", return_value=_Boom([])):
        snap = await ReleaseChecker().fetch()
    assert snap.latest is None
    assert snap.html_url is None


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_non_200_empty() -> None:
    fake = _FakeClient([_FakeResponse(403)])
    with patch("amane.release.httpx.AsyncClient", return_value=fake):
        snap = await ReleaseChecker().fetch()
    assert snap.latest is None


@pytest.mark.asyncio(loop_scope="function")
async def test_fetch_missing_tag_empty() -> None:
    fake = _FakeClient([_FakeResponse(200, {"html_url": "https://example.com"})])
    with patch("amane.release.httpx.AsyncClient", return_value=fake):
        snap = await ReleaseChecker().fetch()
    assert snap.latest is None


@pytest.mark.parametrize("proxy", ["socks5://127.0.0.1:1080", "socks5h://127.0.0.1:1080"])
@pytest.mark.asyncio(loop_scope="function")
async def test_socks_proxy_client_constructs(proxy: str) -> None:
    async with httpx.AsyncClient(proxy=proxy):
        pass


def test_socks4_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown scheme"):
        httpx.AsyncClient(proxy="socks4://127.0.0.1:1080")
