"""测试 OfficialCrawler 路由逻辑."""

from unittest.mock import AsyncMock

import pytest

from amane.aggregate import AggregatedMetadata
from amane.config import SiteConfig
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.official import (
    _ALIAS_TO_MAKER,
    _SERIES_TO_MAKER,
    MANUFACTURER_ALIASES,
    MANUFACTURER_DOMAINS,
    MANUFACTURER_SERIES,
    Manufacturer,
    OfficialCrawler,
    _extract_series_prefix,
    _poster_urls,
)


@pytest.fixture
def crawler():
    client = HttpClient(web=AsyncMock())
    return OfficialCrawler(client=client)


def _config_with_routes(**routes) -> SiteConfig:
    return SiteConfig(official_routes=routes)


# ------------------------------------------------------------------
# 数据完整性
# ------------------------------------------------------------------


def test_all_manufacturers_have_domain():
    """每个厂商都有域名映射."""
    for maker in Manufacturer:
        assert maker in MANUFACTURER_DOMAINS, f"{maker} missing domain"
        assert MANUFACTURER_DOMAINS[maker], f"{maker} domain is empty"


def test_all_manufacturers_have_series():
    """每个厂商都有至少一个系列前缀."""
    for maker in Manufacturer:
        assert maker in MANUFACTURER_SERIES, f"{maker} missing series"
        assert len(MANUFACTURER_SERIES[maker]) > 0, f"{maker} series is empty"


def test_all_manufacturers_have_aliases():
    """每个厂商都有至少一个别名用于匹配."""
    for maker in Manufacturer:
        assert maker in MANUFACTURER_ALIASES, f"{maker} missing aliases"
        assert len(MANUFACTURER_ALIASES[maker]) > 0, f"{maker} aliases is empty"


def test_series_reverse_index_consistency():
    """反向系列索引与正向映射一致."""
    for maker, prefixes in MANUFACTURER_SERIES.items():
        for prefix in prefixes:
            assert _SERIES_TO_MAKER[prefix] == maker, f"{prefix} → {maker}"


def test_alias_reverse_index_coverage():
    """反向别名索引覆盖所有别名."""
    for maker, aliases in MANUFACTURER_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            assert key in _ALIAS_TO_MAKER, f"alias '{alias}' ({maker}) not in reverse index"
            assert _ALIAS_TO_MAKER[key] == maker, f"alias '{alias}' → {_ALIAS_TO_MAKER[key]}, expected {maker}"


# ------------------------------------------------------------------
# _extract_series_prefix
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("SSIS-001", "ssis"),
        ("ssis001", "ssis"),
        ("ABP-123", "abp"),
        ("midv-001", "midv"),
        ("9SSIS001", "ssis"),
        ("12345", ""),
        ("", ""),
    ],
)
def test_extract_series_prefix(number: str, expected: str):
    assert _extract_series_prefix(number) == expected


# ------------------------------------------------------------------
# 路由优先级 1: 用户配置 (config.official_routes)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_by_user_config(crawler):
    """用户配置的前缀映射最高优先级."""
    config = _config_with_routes(ABP=Manufacturer.MOODYZ)
    crawler.config = config
    url = await crawler._search(SearchQuery("ABP-001"))
    assert url == "https://moodyz.com/works/detail/abp001"


@pytest.mark.asyncio
async def test_route_by_user_config_ssis(crawler):
    """用户配置可覆盖内置路由."""
    config = _config_with_routes(SSIS=Manufacturer.MOODYZ)
    crawler.config = config
    url = await crawler._search(SearchQuery("SSIS-497"))
    assert url == "https://moodyz.com/works/detail/ssis497"


@pytest.mark.asyncio
async def test_user_config_takes_priority(crawler):
    """用户配置优先于系列匹配和 studio 推导."""
    # SSIS-001 本应路由到 S1 (s1s1s1.com), 但用户配置覆盖为 Moodyz
    config = _config_with_routes(SSIS=Manufacturer.MOODYZ)
    partial = AggregatedMetadata(number="SSIS-001", studio="S1 NO.1 STYLE")
    query = SearchQuery("SSIS-001", partial_result=partial)
    crawler.config = config
    url = await crawler._search(query)
    assert url == "https://moodyz.com/works/detail/ssis001"


# ------------------------------------------------------------------
# 路由优先级 2: 系列前缀 → 厂商 → 域名
# ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "expected_domain"),
    [
        ("SSIS-001", "s1s1s1.com"),  # S1
        ("MIDV-001", "moodyz.com"),  # MOODYZ
        ("JUL-001", "www.madonna-av.com"),  # Madonna
        ("IPX-001", "ideapocket.com"),  # IdeaPocket
        ("ADN-001", "attackers.net"),  # Attackers
        ("BLK-001", "kirakira-av.com"),  # Kirakira
        ("PPPE-001", "oppai-av.com"),  # OPPAI
        ("WANZ-001", "www.wanz-factory.com"),  # WanzFactory
        ("EBOD-001", "www.av-e-body.com"),  # EBody
        ("DASS-001", "dasdas.jp"),  # Das
        ("CAWD-001", "kawaiikawaii.jp"),  # Kawaii
        ("PRED-001", "premium-beauty.com"),  # PremiumBeauty
        ("MEYD-001", "tameikegoro.jp"),  # Tameikegoro
        ("BF-001", "befreebe.com"),  # BeFree
        ("RBD-001", "attackers.net"),  # Attackers (RBD series)
        ("HND-001", "honnaka.jp"),  # Honnaka
        ("MMND-001", "miman.jp"),  # Miman
        ("MISM-001", "mko-labo.net"),  # MkoLabo
        ("MVSD-001", "mvg.jp"),  # MVG
        ("ROYD-001", "hhh-av.com"),  # Hunter (ROYD series)
        ("CJOD-001", "bi-av.com"),  # Bi
    ],
)
async def test_route_by_series_prefix(crawler, number: str, expected_domain: str):
    """番号系列前缀匹配到正确厂商域名."""
    url = await crawler._search(SearchQuery(number))
    assert url is not None, f"No route for {number}"
    assert expected_domain in url, f"{number}: expected domain {expected_domain}, got {url}"


@pytest.mark.asyncio
async def test_series_case_insensitive(crawler):
    """系列前缀匹配不区分大小写."""
    url = await crawler._search(SearchQuery("ssis-001"))
    assert url == "https://s1s1s1.com/works/detail/ssis001"


# ------------------------------------------------------------------
# 路由优先级 3: partial_result.studio 别名匹配
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_by_studio_alias_s1(crawler):
    """partial_result.studio = 'S1 NO.1 STYLE' 匹配到 S1."""
    partial = AggregatedMetadata(number="SSIS-001", studio="S1 NO.1 STYLE")
    url = await crawler._search(SearchQuery("SSIS-001", partial_result=partial))
    assert "s1s1s1.com" in url


@pytest.mark.asyncio
async def test_route_by_studio_alias_japanese(crawler):
    """日语 studio 名称匹配."""
    partial = AggregatedMetadata(number="JUL-001", studio="マドンナ")
    url = await crawler._search(SearchQuery("JUL-001", partial_result=partial))
    assert "www.madonna-av.com" in url


@pytest.mark.asyncio
async def test_route_by_studio_alias_premium(crawler):
    """前序爬虫返回 'プレミアム' 匹配到 PremiumBeauty."""
    partial = AggregatedMetadata(number="PRED-001", studio="プレミアム")
    url = await crawler._search(SearchQuery("PRED-001", partial_result=partial))
    assert "premium-beauty.com" in url


@pytest.mark.asyncio
async def test_route_by_studio_alias_moodyz(crawler):
    """partial_result.studio = 'MOODYZ' 匹配."""
    partial = AggregatedMetadata(number="MIDV-001", studio="MOODYZ")
    url = await crawler._search(SearchQuery("MIDV-001", partial_result=partial))
    assert "moodyz.com" in url


@pytest.mark.asyncio
async def test_route_by_studio_substring_match(crawler):
    """studio 名称包含别名时也能匹配 (子串匹配)."""
    partial = AggregatedMetadata(number="IPX-001", studio="IDEA POCKET (アイデアポケット)")
    url = await crawler._search(SearchQuery("IPX-001", partial_result=partial))
    assert "ideapocket.com" in url


# ------------------------------------------------------------------
# 未匹配
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_no_match_returns_none(crawler):
    """无匹配规则时返回 None."""
    # 无配置, 系列前缀不在映射中, 无 partial_result
    url = await crawler._search(SearchQuery("XXXXX-999"))
    assert url is None


@pytest.mark.asyncio
async def test_route_no_match_with_unknown_studio(crawler):
    """未知 studio 名称也返回 None."""
    partial = AggregatedMetadata(number="XXXXX-999", studio="Some Unknown Studio")
    url = await crawler._search(SearchQuery("XXXXX-999", partial_result=partial))
    assert url is None


# ------------------------------------------------------------------
# 优先级验证
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_series_beats_studio_when_both_match(crawler):
    """系列前缀匹配优先于 studio 别名匹配 (但两者结果相同时等价)."""
    # MIDV → Moodyz (by series), studio "S1" → S1 (by alias)
    # 系列匹配优先, 所以路由到 Moodyz
    partial = AggregatedMetadata(number="MIDV-001", studio="S1 NO.1 STYLE")
    url = await crawler._search(SearchQuery("MIDV-001", partial_result=partial))
    assert "moodyz.com" in url  # series match wins


# ------------------------------------------------------------------
# 图片: 竖版海报推导
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("thumb_url", "expected"),
    [
        (
            "https://cdn.up-timely.com/image/2/content/8903/sivr067-pl.jpg",
            ["https://cdn.up-timely.com/image/2/content/8903/sivr067-ps.jpg"],
        ),
        # 新目录是哈希文件名, 没有可推导的竖版地址
        ("https://cdn.up-timely.com/image/2/content/72658/jYYpm41QBnuyDREA7Mnf8yIc0T3PAwmRSHA1welb.jpg", []),
        ("https://cdn.up-timely.com/image/2/content/8903/sivr067-ps.jpg", []),
        (None, []),
    ],
)
def test_poster_urls(thumb_url: str | None, expected: list[str]):
    assert _poster_urls(thumb_url) == expected
