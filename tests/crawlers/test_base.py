"""测试 crawler 基类, 模型和注册表"""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from amane.config.manager import SiteConfig
from amane.crawlers.actor import ActorCrawler, ActorMetadata
from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.block import FailureReason, classify_block, classify_request_error
from amane.crawlers.http import HttpClient, RequestError
from amane.crawlers.models import MediaMetadata, SearchQuery
from amane.crawlers.registry import CrawlerRegistry
from amane.net.errors import FailureKind, RequestFailure

if TYPE_CHECKING:
    from amane.enums import SiteName


class FakeCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=cast("SiteName", "fake"), base_url="https://fake.example.com")

    def __init__(self, client: HttpClient | None = None, config: SiteConfig | None = None):
        super().__init__(client=client or AsyncMock(spec=HttpClient), config=config)

    async def _search(self, query: SearchQuery, options=None) -> str | None:
        return f"{self.base_url}/v/{query.number}"

    async def _scrape(self, url: str, options=None) -> MediaMetadata | None:
        return MediaMetadata(number="TEST-001", title="Fake Result", source_url=url)


# --- Crawler 基类 ---


class TestCrawlerBase:
    @pytest.mark.asyncio
    async def test_fetch(self):
        result = await FakeCrawler(client=None).fetch(SearchQuery("TEST-001"))
        assert result is not None
        assert result.number == "TEST-001"
        assert result.source_url == "https://fake.example.com/v/TEST-001"

    @pytest.mark.asyncio
    async def test_fetch_no_search_result(self):
        class NoResultCrawler(FakeCrawler):
            async def _search(self, query, options=None):
                return None

        result = await NoResultCrawler(client=None).fetch(SearchQuery("X"))
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_search_request_error(self):
        class ErrorSearchCrawler(FakeCrawler):
            async def _search(self, query, options=None):
                raise RequestError("https://x.com", "timeout")

        with pytest.raises(RequestError, match="timeout"):
            await ErrorSearchCrawler(client=None).fetch(SearchQuery("TEST-001"))

    @pytest.mark.asyncio
    async def test_fetch_scrape_request_error(self):
        class ErrorScrapeCrawler(FakeCrawler):
            async def _scrape(self, url, options=None):
                raise RequestError(url, "timeout")

        with pytest.raises(RequestError, match="timeout"):
            await ErrorScrapeCrawler(client=None).fetch(SearchQuery("TEST-001"))

    @pytest.mark.asyncio
    async def test_fetch_scrape_returns_none(self):
        class NoneScrapeCrawler(FakeCrawler):
            async def _scrape(self, url, options=None):
                return None

        result = await NoneScrapeCrawler(client=None).fetch(SearchQuery("TEST-001"))
        assert result is None


# --- classify_block ---


@pytest.mark.parametrize(
    "text,failure,expected",
    [
        # HTTP 403 (结构化 failure, 不解析文本)
        ("some page content", RequestFailure(kind=FailureKind.HTTP_STATUS, status=403, message="HTTP 403"), True),
        # HTTP 404 / 429 / 500 同样走结构化状态分类
        ("some page content", RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404"), True),
        ("some page content", RequestFailure(kind=FailureKind.HTTP_STATUS, status=429, message="HTTP 429"), True),
        ("some page content", RequestFailure(kind=FailureKind.HTTP_STATUS, status=503, message="HTTP 503"), True),
        # Empty response
        ("", None, True),
        # Geo-restricted
        ("This content is not available in your region", None, True),
        ("お住まいの地域からはご利用になれません", None, True),
        # IP banned
        ("Your IP has been banned your access", None, True),
        # Cloudflare
        ("<html>cf-ray-id:abc123</html>", None, True),
        # Cloudflare challenge
        ("just a moment... cloudflare challenge", None, True),
        # Age verification redirect
        ("redirecting... driver-verify", None, True),
        # Age verification page (Japanese)
        ("年齢認証が必要です", None, True),
        # Age verification page (English)
        ("This site requires age verification", None, True),
        # Normal responses
        ("<html><body>Normal Page</body></html>", None, False),
        ("Some content without blocked patterns", None, False),
        # HTTP 403 text without structured failure - NOT blocked (语义只从结构化字段取)
        ("Server returned HTTP 403", None, False),
    ],
)
def test_classify_block_detects(text: str, failure: RequestFailure | None, expected: bool):
    reason = classify_block(text, failure=failure)
    assert (reason is not None) is expected


_CLOUDFLARE_REASON_CASES: list[tuple[str, FailureReason | None]] = [
    ("just a moment... cloudflare challenge", FailureReason.CLOUDFLARE_CHALLENGE),
    (
        "<html><head><title>しばらくお待ちください...</title>"
        '<script>window._cf_chl_opt={cType:"managed"};</script></head><body>captcha</body></html>',
        FailureReason.CLOUDFLARE_CHALLENGE,
    ),
    ("<html><div class='ray-id'>Ray ID: abc</div>cf-ray blocked</html>", FailureReason.CLOUDFLARE_BLOCKED),
    ("<html><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script></html>", None),
    ('<html><input name="cf-turnstile-response"></html>', None),
]


@pytest.mark.parametrize(("text", "expected"), _CLOUDFLARE_REASON_CASES)
def test_classify_block_cloudflare_reason(text: str, expected: FailureReason | None):
    assert classify_block(text) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, FailureReason.HTTP_ERROR),
        (404, FailureReason.NOT_FOUND),
        (429, FailureReason.RATE_LIMITED),
        (500, FailureReason.SERVER_ERROR),
        (503, FailureReason.SERVER_ERROR),
    ],
)
def test_classify_block_status_reason(status: int, expected: FailureReason):
    """HTTP 状态分类语义: 404 非拦截, 429 限速, 5xx 服务端错误 (仅空正文/无正文信号时按状态分类)."""
    failure = RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}")
    assert classify_block("", failure=failure) == expected


@pytest.mark.parametrize(
    ("text", "status", "expected"),
    [
        # 正文信号优先于状态码: 403 封禁页含 "banned your access" (javdb 真实场景)
        (
            "The owner of this website has banned your access based on your browser's behaving",
            403,
            FailureReason.IP_BANNED,
        ),
        # 403 + Cloudflare 挑战页 → 挑战拦截, 不是笼统的 http_error
        ("<html>Just a moment... cloudflare</html>", 403, FailureReason.CLOUDFLARE_CHALLENGE),
        # 403 + 年龄验证 → age_verification
        ("<html>年齢認証が必要です</html>", 403, FailureReason.AGE_VERIFICATION),
        # 403 + 地域提示 → geo_restricted
        ("This content is not available in your region", 403, FailureReason.GEO_RESTRICTED),
        # 正常 404 页 (无正文信号) → 按状态分类 not_found
        ("<html><body>404 Not Found</body></html>", 404, FailureReason.NOT_FOUND),
    ],
)
def test_classify_block_text_precedes_status(text: str, status: int, expected: FailureReason):
    """正文启发式优先于状态码: 状态码只在正文无法判定时分类."""
    failure = RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}")
    assert classify_block(text, failure=failure) == expected


def test_classify_block_failure_body_text_precedes_status():
    """失败响应正文 (403 封禁页) 优先于状态码分类 - task-3/task-5 真实场景."""
    body = b"The owner of this website has banned your access based on your browser's behaving\nIP: 103.156.242.198"
    failure = RequestFailure(kind=FailureKind.HTTP_STATUS, status=403, message="HTTP 403", body=body)
    # 异常路径 (get_text 抛 RequestError) 与正文路径走同一判定
    assert classify_block("", failure=failure) == FailureReason.IP_BANNED
    assert classify_request_error(failure) == FailureReason.IP_BANNED


def test_classify_request_error_body_without_signal_falls_back_to_status():
    """失败正文无拦截信号时回退到状态码分类."""
    failure = RequestFailure(
        kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404", body=b"<html><body>404 Not Found</body></html>"
    )
    assert classify_request_error(failure) == FailureReason.NOT_FOUND


# --- _resolve_config ---


class TestResolveConfig:
    def test_default_base_url_no_config(self):
        c = FakeCrawler(client=None)
        c._resolve_config()
        assert c.base_url == "https://fake.example.com"
        assert c.cookies == {}

    def test_custom_base_url(self):
        c = FakeCrawler(client=None)
        c.config = SiteConfig(base_url="https://mirror.example.com/")
        c._resolve_config()
        assert c.base_url == "https://mirror.example.com"

    def test_cookie_parsing(self):
        c = FakeCrawler(client=None)
        c.config = SiteConfig(cookie={"key1": "val1", "key2": "val2"})
        c._resolve_config()
        assert c.cookies == {"key1": "val1", "key2": "val2"}

    def test_cookies_merge_with_defaults(self):
        class CookieCrawler(FakeCrawler):
            @classmethod
            def profile(cls) -> CrawlerProfile:
                return CrawlerProfile(
                    name=cast("SiteName", "cookie_fake"),
                    base_url="https://fake.example.com",
                    cookies={"default_key": "default_val"},
                )

        c = CookieCrawler(client=None)
        c.config = SiteConfig(cookie={"user_key": "user_val"})
        c._resolve_config()
        assert c.cookies == {"default_key": "default_val", "user_key": "user_val"}

    def test_empty_cookie_field(self):
        c = FakeCrawler(client=None)
        c.config = SiteConfig(cookie={})
        c._resolve_config()
        assert c.cookies == {}


# --- 注册表 ---


class TestCrawlerRegistry:
    def test_register_and_get(self):
        r = CrawlerRegistry()
        r.register(FakeCrawler)
        assert r.get("fake") is FakeCrawler
        assert "fake" in list(r.sites())

    def test_unknown_returns_none(self):
        assert CrawlerRegistry().get("x") is None


class FakeActorCrawler(ActorCrawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=cast("SiteName", "fake_actor"), base_url="https://actor.example.com")

    async def _search(self, name: str) -> str | None:
        return f"{self.base_url}/{name}"

    async def _scrape(self, url: str) -> ActorMetadata | None:
        return None


@pytest.mark.asyncio
async def test_actor_fetch_does_not_swallow_unexpected():
    class BoomActor(FakeActorCrawler):
        async def _search(self, name: str) -> str | None:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await BoomActor(client=AsyncMock(spec=HttpClient)).fetch("Alice")


@pytest.mark.asyncio
async def test_actor_fetch_bubbles_request_error():
    class ErrActor(FakeActorCrawler):
        async def _search(self, name: str) -> str | None:
            raise RequestError("https://x.com", "timeout")

    with pytest.raises(RequestError, match="timeout"):
        await ErrActor(client=AsyncMock(spec=HttpClient)).fetch("Alice")


class TestSearchQueryAlternates:
    """别名候选在构造期收口: 同形与空项丢弃, 顺序保留."""

    def test_drops_duplicates_and_self(self):
        query = SearchQuery("300MIUM-123", alternate_numbers=("MIUM-123", "300mium123", " MIUM-123 ", ""))
        assert query.alternate_numbers == ("MIUM-123",)

    def test_keeps_order(self):
        query = SearchQuery("A-1", alternate_numbers=("B-2", "C-3"))
        assert query.alternate_numbers == ("B-2", "C-3")

    def test_empty_by_default(self):
        assert SearchQuery("A-1").alternate_numbers == ()

    def test_underscore_is_not_folded(self):
        """下划线不当短横线: 010115_001 与 010115-001 是两部片."""
        query = SearchQuery("010115-001", alternate_numbers=("010115_001",))
        assert query.alternate_numbers == ("010115_001",)
