"""指定网址刮削的来源判定."""

from __future__ import annotations

import pytest

from amane.config import SiteConfig
from amane.crawlers.url_routing import resolve_url_source


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://javdb.com/v/abcd1", "javdb"),
        ("HTTPS://WWW.JAVDB.COM/v/abcd1", "javdb"),
        ("http://javdb.com/v/abcd1", "javdb"),
        ("https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=midv00123/", "dmm"),
        ("https://attackers.net/works/detail/abc", "official"),
        ("https://faleno.jp/top/works/abc-001/", "faleno"),
        ("https://javdb.com:8443/v/abcd1", "javdb"),
    ],
)
def test_resolves_declared_host(url: str, expected: str) -> None:
    assert resolve_url_source(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://theporndb.net/scenes/123",
        "https://r18.dev/videos/vod/movies/detail/-/dvd_id=abc",
        "https://www.prestige-av.com/item/abc",
        "https://unknown.example/detail/1",
        "https://javdb.com.evil.example/v/abcd1",
        "not a url",
        "https://",
        "",
    ],
)
def test_rejects_unknown_or_unsupported(url: str) -> None:
    assert resolve_url_source(url) is None


def test_configured_base_url_wins_over_other_sources() -> None:
    """用户改写入口后, 该域名归被配置的来源."""
    configs = {"javdb": SiteConfig(base_url="https://www.dmm.co.jp")}
    assert resolve_url_source("https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=midv00123/", configs) == "javdb"
