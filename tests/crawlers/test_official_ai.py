"""AI 官网源单元测试: 应答解析、凭据守卫与候选变体顺序."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.aggregate.models import AggregatedMetadata
from amane.config.manager import SiteConfig
from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.official_ai import (
    AiOfficialCrawler,
    _clean_studio,
    _parse_domains,
    _registrable,
    _search_terms,
    _title_ratio,
)
from amane.net.errors import FailureKind, RequestError, RequestFailure

URL = "https://example.com/"


def _reply(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def _crawler(*, route: Callable[[str], str] | None = None, config: SiteConfig | None = None):
    web = AsyncMock()

    async def get_text(url: str, **kwargs: object) -> str:
        return route(url) if route else ""

    web.get_text.side_effect = get_text
    return AiOfficialCrawler(client=HttpClient(web=web), config=config)


def _query(title: str = "Melody Marks 2 Full Romp", studio: str = "FansDB: Dreddxxx (onlyfans)") -> SearchQuery:
    partial = AggregatedMetadata(number="dreddxxx.melody.maks.xmas", title=title, studio=studio)
    return SearchQuery(number="dreddxxx.melody.maks.xmas", partial_result=partial)


@pytest.mark.parametrize(
    ("label", "content", "expected"),
    [
        ("裸数组", '["dreddxxx.com","dreddxxx.net"]', ["dreddxxx.com", "dreddxxx.net"]),
        # 回复常带说明文字, 只认第一个 JSON 数组.
        ("带说明", 'Here are candidates:\n["a-studio.com"]\nHope this helps.', ["a-studio.com"]),
        ("去重", '["a.com","a.com"]', ["a.com"]),
        ("没有数组", "I could not determine the domain.", []),
        ("非法域名", '["not a domain"]', []),
    ],
)
def test_parse_domains(label: str, content: str, expected: list[str]) -> None:
    assert _parse_domains(_reply(content)) == expected, label


def test_parse_domains_rejects_bad_shape() -> None:
    assert _parse_domains({}) == []
    assert _parse_domains(None) == []
    assert _parse_domains({"choices": []}) == []


def test_no_token_sends_no_request() -> None:
    """没配凭据时不发请求: 这类来源默认关闭, 不能因为缺配置就去打模型."""
    web = AsyncMock()
    crawler = AiOfficialCrawler(client=HttpClient(web=web), config=SiteConfig())
    assert crawler._candidate_urls([]) == []
    assert crawler.config is not None
    assert crawler.config.api_token is None


def test_candidates_cover_common_prefixes() -> None:
    """模型给裸名时, 官网常带 official 前缀, 候选里必须覆盖."""
    crawler = _crawler()
    urls = crawler._candidate_urls(["dreddxxx.com"])
    assert "https://dreddxxx.com/" in urls
    assert "https://www.dreddxxx.com/" in urls
    assert "https://officialdreddxxx.com/" in urls


def test_candidates_capped() -> None:
    crawler = _crawler()
    urls = crawler._candidate_urls(["a.com", "b.com", "c.com", "d.com"])
    assert len(urls) == 12  # 3 个域名 × 4 种变体


@pytest.mark.asyncio
async def test_dead_domain_falls_through() -> None:
    """候选域不存在时换下一个, 不是整次失败."""
    crawler = _crawler(
        route=lambda url: (_ for _ in ()).throw(
            RequestError(url, RequestFailure(kind=FailureKind.CURL, message="could not resolve"))
        )
    )
    assert await crawler.fetch(_query()) is None


@pytest.mark.parametrize(
    ("label", "raw", "expected"),
    [
        # TPDB 的来源前缀与平台后缀都要剥掉, 否则模型会去猜 onlyfans.com.
        ("来源前缀+平台后缀", "FansDB: Dreddxxx (onlyfans)", "Dreddxxx"),
        ("平台后缀", "Dreddxxx (OnlyFans)", "Dreddxxx"),
        ("来源前缀", "FansDB: Dreddxxx", "Dreddxxx"),
        ("原样", "Brazzers", "Brazzers"),
    ],
)
def test_clean_studio(label: str, raw: str, expected: str) -> None:
    assert _clean_studio(raw) == expected, label


@pytest.mark.parametrize(
    ("label", "domain", "expected"),
    [
        ("子域归一", "www.onlyfans.com", "onlyfans.com"),
        ("裸域", "onlyfans.com", "onlyfans.com"),
        ("两段", "dreddxxx.com", "dreddxxx.com"),
    ],
)
def test_registrable(label: str, domain: str, expected: str) -> None:
    assert _registrable(domain) == expected, label


def test_platform_domains_filtered_from_candidates() -> None:
    """模型常把平台站当厂牌官网; 候选里必须滤掉, 否则只会抓到平台首页."""
    crawler = _crawler()
    urls = crawler._candidate_urls(["onlyfans.com", "dreddxxx.com"])
    assert all("onlyfans.com" not in url for url in urls)
    assert any("dreddxxx.com" in url for url in urls)


@pytest.mark.parametrize(
    ("label", "title", "expected"),
    [
        # 虚词不参与搜索, 否则站内命中一堆无关结果.
        ("去虚词", "Melody Marks 2 Full Romp", "melody marks 2"),
        ("截断", "One Two Three Four Five Six", "one two three"),
        ("标点转空格", "Lana-Gee & Alana Rose", "lana gee alana"),
    ],
)
def test_search_terms(label: str, title: str, expected: str) -> None:
    assert _search_terms(title) == expected, label


@pytest.mark.parametrize(
    ("label", "db_title", "site_title", "expected"),
    [
        # 官网标题常是数据库标题的前缀, 这种情况必须给满分.
        ("前缀", "Melody Marks 2 Full Romp", "Melody Marks #2 - The Official Dredd XXX", 1.0),
        # 一个标题是另一个的前缀时算满分.
        ("前缀扩展", "Melody Marks 2", "Melody Marks 2 Full Romp", 1.0),
        # 同系列不同集不能互相当成命中.
        ("不同集", "Melody Marks 2 Full Romp", "Chloe Amour #2", 0.0),
    ],
)
def test_title_ratio(label: str, db_title: str, site_title: str, expected: float) -> None:
    assert _title_ratio(db_title, site_title) == expected, label
