"""验证 build_network_stack 把声明同源 Referer 的站点 host 交给 WebClient."""

import httpx2 as httpx

from amane.app.runtime import build_network_stack
from amane.config import HotSettings
from amane.config.manager import SiteConfig
from amane.crawlers import actor_registry, registry
from amane.crawlers.base import CrawlerProfile


def _all_profiles() -> list[tuple[str, CrawlerProfile]]:
    profiles: list[tuple[str, CrawlerProfile]] = []
    for site in registry.sites():
        crawler_cls = registry.get(site)
        if crawler_cls:
            profiles.append((site, crawler_cls.profile()))
    for name in actor_registry.sites():
        crawler_cls = actor_registry.get(name)
        if crawler_cls:
            profiles.append((name, crawler_cls.profile()))
    return profiles


def _hosts() -> frozenset[str]:
    return build_network_stack(HotSettings()).web_client._same_origin_referer_hosts


def test_declared_site_hosts_are_registered():
    hosts = _hosts()
    assert "www.javbus.com" in hosts
    for site, profile in _all_profiles():
        if not profile.same_origin_referer:
            continue
        for url in [*profile.urls, profile.base_url]:
            assert httpx.URL(url).host in hosts, f"{site}: {url}"


def test_undeclared_site_host_is_absent():
    hosts = _hosts()
    assert "www.dmm.co.jp" not in hosts
    assert "javdb.com" not in hosts


def test_configured_mirror_host_is_registered():
    """SiteConfig.base_url 指向镜像时, 镜像 host 同样注入 Referer."""
    hot = HotSettings()
    hot.scraping.site_config["javbus"] = SiteConfig(base_url="https://mirror.example.com")

    hosts = build_network_stack(hot).web_client._same_origin_referer_hosts

    assert "mirror.example.com" in hosts
    assert "www.javbus.com" in hosts
