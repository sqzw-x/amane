"""HttpClient: HTML 拦截启发式, 浏览器渲染契约与按来源路由."""

from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.http import HttpClient
from amane.enums import BrowserBackendName
from amane.net.connectivity import ConnectivityStatus
from amane.net.errors import FailureKind, FailureReason, RequestError, RequestFailure, SourceError

_URL = "https://example.com/spa"


@pytest.fixture
def mock_web():
    return AsyncMock()


@pytest.fixture
def mock_browser():
    return AsyncMock()


@pytest.fixture
def client(mock_web, mock_browser):
    return HttpClient(web=mock_web, browser=mock_browser)


@pytest.mark.asyncio
async def test_get_html_passes_through_normal_page(client, mock_web):
    mock_web.get_text.return_value = "<html><body>Normal Page</body></html>"
    assert await client.get_html("https://example.com/") == "<html><body>Normal Page</body></html>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("html", "reason"),
    [
        ("", FailureReason.EMPTY_RESPONSE),
        ("just a moment... cloudflare challenge", FailureReason.CLOUDFLARE_CHALLENGE),
        ("This content is not available in your region", FailureReason.GEO_RESTRICTED),
    ],
)
async def test_get_html_raises_source_error_on_block(client, mock_web, html: str, reason: FailureReason):
    mock_web.get_text.return_value = html
    with pytest.raises(SourceError) as exc:
        await client.get_html("https://example.com/")
    assert exc.value.reason == reason


@pytest.mark.asyncio
async def test_get_rendered_returns_page_with_host_scope(client, mock_browser):
    mock_browser.get_page.return_value = ("<html><body>Rendered</body></html>", None)

    assert await client.get_rendered(_URL) == "<html><body>Rendered</body></html>"

    call = mock_browser.get_page.await_args
    assert call.args == (_URL,)
    assert call.kwargs["scope"] == "example.com"
    assert call.kwargs["backend"] is None
    assert call.kwargs["timeout"] == 30000.0


# 后端失败已带结构化语义: 超时与未解决的挑战不能退化成通用错误.
_FAILURE_CASES: list[tuple[RequestFailure, FailureReason]] = [
    (RequestFailure(kind=FailureKind.TIMEOUT, message="timeout"), FailureReason.TIMEOUT),
    (
        RequestFailure(kind=FailureKind.UNEXPECTED, message="challenge", reason=FailureReason.CLOUDFLARE_CHALLENGE),
        FailureReason.CLOUDFLARE_CHALLENGE,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "reason"), _FAILURE_CASES)
async def test_get_rendered_maps_backend_failure(client, mock_browser, failure: RequestFailure, reason: FailureReason):
    mock_browser.get_page.return_value = (None, failure)

    with pytest.raises(RequestError) as exc:
        await client.get_rendered(_URL)

    assert (exc.value.reason, exc.value.message) == (reason, failure.message)


@pytest.mark.asyncio
async def test_get_rendered_rejects_block_page(client, mock_browser):
    mock_browser.get_page.return_value = ("<html>just a moment... cloudflare</html>", None)

    with pytest.raises(SourceError) as exc:
        await client.get_rendered(_URL)

    assert exc.value.reason == FailureReason.CLOUDFLARE_CHALLENGE


@pytest.mark.asyncio
async def test_get_rendered_no_browser_raises(mock_web):
    client = HttpClient(web=mock_web, browser=None)

    with pytest.raises(RequestError, match="browser backend disabled") as exc:
        await client.get_rendered(_URL)

    assert exc.value.reason == FailureReason.UNEXPECTED


def test_for_source_without_use_browser_returns_same_client(mock_web, mock_browser):
    client = HttpClient(web=mock_web, browser=mock_browser)

    assert client.for_source("javdb", SiteConfig()) is client
    assert client.for_source("javdb", None) is client


@pytest.mark.asyncio
async def test_use_browser_source_routes_get_html_through_browser(mock_web, mock_browser):
    client = HttpClient(web=mock_web, browser=mock_browser, browser_timeout=12345)
    view = client.for_source(
        "minnano",
        SiteConfig(use_browser=True, browser_backend=BrowserBackendName.CAMOUFOX),
    )
    mock_browser.get_page.return_value = ("<html><body>ok</body></html>", None)

    text = await view.get_html("https://www.minnano-av.com/search_result.php", cookies={"a": "b"})

    assert text == "<html><body>ok</body></html>"
    mock_web.get_text.assert_not_awaited()
    call = mock_browser.get_page.await_args
    assert call.args[0] == "https://www.minnano-av.com/search_result.php"
    assert call.kwargs["scope"] == "minnano"
    assert call.kwargs["backend"] is BrowserBackendName.CAMOUFOX
    assert call.kwargs["cookies"] == {"a": "b"}
    assert call.kwargs["timeout"] == 12345


# 连通性探测与刮削同形态: 渲染视图落浏览器, 结论复用同一套失败语义.

_RENDER_CHECK_CASES: list[tuple[str | None, RequestFailure | None, ConnectivityStatus, FailureReason | None]] = [
    ("<html>ok</html>", None, ConnectivityStatus.OK, None),
    (
        None,
        RequestFailure(kind=FailureKind.UNEXPECTED, message="challenge", reason=FailureReason.CLOUDFLARE_CHALLENGE),
        ConnectivityStatus.FAILED,
        FailureReason.CLOUDFLARE_CHALLENGE,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("html", "failure", "status", "reason"), _RENDER_CHECK_CASES)
async def test_check_uses_browser_for_render_view(
    mock_web,
    mock_browser,
    html: str | None,
    failure: RequestFailure | None,
    status: ConnectivityStatus,
    reason: FailureReason | None,
):
    client = HttpClient(web=mock_web, browser=mock_browser)
    view = client.for_source("minnano", SiteConfig(use_browser=True))
    mock_browser.get_page.return_value = (html, failure)

    outcome = await view.check(_URL)

    assert (outcome.status, outcome.reason) == (status, reason)
    mock_web.request.assert_not_awaited()
