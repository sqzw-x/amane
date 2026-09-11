"""
数据驱动的 crawler 测试 - 自动发现 cases/ 下的 TOML 用例.

添加新用例只需创建 .toml 文件 + 响应文件, 无需修改 Python 代码.

TOML 格式:
    site = "dmm"                          # 注册表中的 crawler 名称

    [search]                              # 可选: 测试 search()
    number = "SSIS-497"
    responses = [{ url_contains = "...", file = "response.html" }]
    expected_urls = [...]                  # 可选: 精确匹配
    expected_none = true                   # 可选: 期望 _search 返回 None (空搜索 / 拦截页)

    [scrape]                              # 可选: 测试 scrape()
    url = "https://..."
    responses = [{ url_contains = "...", method = "post_json", file = "response.json" }]

    [scrape.expected]                     # 字段断言
    field = value                         # 精确匹配
    field_contains = "x"                  # 子串/成员检查
    field_count = N                       # len(field) == N
    field_not_empty = true                # 真值检查
    field_is_none = true                  # None 检查
    field_between = [low, high]           # 数值闭区间
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers import registry
from amane.crawlers.models import SearchQuery
from amane.net.errors import SourceError

from .driven import assert_expected, build_mock, discover_film_cases, http_client, load_toml

CASES = discover_film_cases(lambda site: registry.get(site) is not None)

if not CASES:
    pytest.skip("no test cases found", allow_module_level=True)


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_crawler_scrape(case_id: str, toml_path: Path) -> None:
    """数据驱动的 _scrape 测试 - 读取 TOML 配置, mock HTTP, 断言字段."""
    config = load_toml(toml_path)
    if "scrape" not in config:
        pytest.skip("no [scrape] section")

    site = config["site"]
    crawler_cls = registry.get(site)
    assert crawler_cls is not None, f"Unknown crawler site: {site!r}"

    mock_web = AsyncMock()
    crawler = crawler_cls(client=http_client(mock_web))

    scrape_cfg = config["scrape"]
    build_mock(mock_web, toml_path.parent, scrape_cfg["responses"])

    result = await crawler._scrape(scrape_cfg["url"])
    assert result is not None, f"_scrape() returned None for {scrape_cfg['url']}"
    assert_expected(result, scrape_cfg["expected"])


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_crawler_fetch(case_id: str, toml_path: Path) -> None:
    """数据驱动的 fetch 测试 - 测试完整 fetch() 流程 (含 config)."""
    config = load_toml(toml_path)
    if "fetch" not in config:
        pytest.skip("no [fetch] section")

    site = config["site"]
    crawler_cls = registry.get(site)
    assert crawler_cls is not None, f"Unknown crawler site: {site!r}"

    fetch_cfg = config["fetch"]
    site_config = SiteConfig(**fetch_cfg.get("config", {})) if fetch_cfg.get("config") else None

    mock_web = AsyncMock()
    crawler = crawler_cls(client=http_client(mock_web), config=site_config)

    build_mock(mock_web, toml_path.parent, fetch_cfg["responses"])

    result = await crawler.fetch(SearchQuery(fetch_cfg["number"]))
    assert result is not None, f"fetch() returned None for {fetch_cfg['number']}"
    assert_expected(result, fetch_cfg["expected"])


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_crawler_search(case_id: str, toml_path: Path) -> None:
    """数据驱动的 search 测试 - 读取 TOML 配置, mock HTTP, 断言 URL."""
    config = load_toml(toml_path)
    if "search" not in config:
        pytest.skip("no [search] section")

    site = config["site"]
    crawler_cls = registry.get(site)
    assert crawler_cls is not None, f"Unknown crawler site: {site!r}"

    mock_web = AsyncMock()
    crawler = crawler_cls(client=http_client(mock_web))

    search_cfg = config["search"]
    build_mock(mock_web, toml_path.parent, search_cfg["responses"])

    try:
        url = await crawler._search(SearchQuery(search_cfg["number"]))
    except SourceError:
        # 拦截页由 get_html 抛出; 直接测 _search 时与「无 URL」同类
        if search_cfg.get("expected_none"):
            return
        raise

    if search_cfg.get("expected_none"):
        assert url is None, f"_search({search_cfg['number']!r}) should return None, got {url!r}"
    elif "expected_urls" in search_cfg:
        assert url == search_cfg["expected_urls"][0]
    else:
        assert url is not None, f"_search({search_cfg['number']!r}) returned None"
