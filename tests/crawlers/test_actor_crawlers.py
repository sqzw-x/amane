"""数据驱动的演员爬虫测试 - 发现 cases/ 下 actor_registry 站点的 TOML.

与影片用例同一套 TOML 约定, 查询键是 name 而非 number.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.actor import GFriendsActorCrawler, actor_registry
from amane.crawlers.actor.base import ActorCrawler
from amane.crawlers.http import HttpClient
from amane.net.errors import SourceError

from .driven import assert_expected, build_mock, discover_actor_cases, http_client, load_toml

CASES = discover_actor_cases(lambda site: actor_registry.get(site) is not None)

if not CASES:
    pytest.skip("no test cases found", allow_module_level=True)


def _site_config(section: dict) -> SiteConfig | None:
    raw = section.get("config")
    return SiteConfig(**raw) if isinstance(raw, dict) else None


def _actor(site: str, client: HttpClient, *, data_dir: Path, config: SiteConfig | None = None) -> ActorCrawler:
    cls = actor_registry.get(site)
    assert cls is not None, f"Unknown actor crawler site: {site!r}"
    if cls is GFriendsActorCrawler:
        return GFriendsActorCrawler(client=client, data_dir=data_dir, config=config)
    return cls(client=client, config=config)


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_actor_scrape(case_id: str, toml_path: Path, tmp_path: Path) -> None:
    config = load_toml(toml_path)
    if "scrape" not in config:
        pytest.skip("no [scrape] section")

    scrape_cfg = config["scrape"]
    mock_web = AsyncMock()
    crawler = _actor(config["site"], http_client(mock_web), data_dir=tmp_path, config=_site_config(scrape_cfg))
    build_mock(mock_web, toml_path.parent, scrape_cfg["responses"])

    result = await crawler._scrape(scrape_cfg["url"])
    assert result is not None, f"_scrape() returned None for {scrape_cfg['url']}"
    assert_expected(result, scrape_cfg["expected"])


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_actor_fetch(case_id: str, toml_path: Path, tmp_path: Path) -> None:
    config = load_toml(toml_path)
    if "fetch" not in config:
        pytest.skip("no [fetch] section")

    fetch_cfg = config["fetch"]
    mock_web = AsyncMock()
    crawler = _actor(config["site"], http_client(mock_web), data_dir=tmp_path, config=_site_config(fetch_cfg))
    build_mock(mock_web, toml_path.parent, fetch_cfg["responses"])

    result = await crawler.fetch(fetch_cfg["name"])
    if fetch_cfg.get("expected_none"):
        assert result is None, f"fetch({fetch_cfg['name']!r}) should return None, got {result!r}"
        return
    assert result is not None, f"fetch() returned None for {fetch_cfg['name']}"
    assert_expected(result, fetch_cfg["expected"])


@pytest.mark.parametrize("case_id,toml_path", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_actor_search(case_id: str, toml_path: Path, tmp_path: Path) -> None:
    config = load_toml(toml_path)
    if "search" not in config:
        pytest.skip("no [search] section")

    search_cfg = config["search"]
    mock_web = AsyncMock()
    crawler = _actor(config["site"], http_client(mock_web), data_dir=tmp_path, config=_site_config(search_cfg))
    build_mock(mock_web, toml_path.parent, search_cfg["responses"])

    try:
        url = await crawler._search(search_cfg["name"])
    except SourceError:
        if search_cfg.get("expected_none"):
            return
        raise

    if search_cfg.get("expected_none"):
        assert url is None, f"_search({search_cfg['name']!r}) should return None, got {url!r}"
    elif "expected_urls" in search_cfg:
        assert url == search_cfg["expected_urls"][0]
    else:
        assert url is not None, f"_search({search_cfg['name']!r}) returned None"
