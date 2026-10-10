"""验证 build_network_stack 把站点级代理落到该来源的 host 上."""

import httpx2 as httpx
import pytest

from amane.app.runtime import build_network_stack
from amane.config import HotSettings
from amane.config.manager import SiteConfig
from amane.crawlers import registry
from amane.enums import SiteName

_GLOBAL_PROXY = "http://127.0.0.1:7890"
_SITE_PROXY = "socks5://127.0.0.1:1080"
_SITE = SiteName.JAVDB


def _site_hosts(site: SiteName) -> list[str]:
    crawler_cls = registry.get(str(site))
    assert crawler_cls is not None
    profile = crawler_cls.profile()
    return [url for url in (*profile.urls, profile.base_url) if url]


@pytest.mark.asyncio
async def test_site_proxy_applies_to_every_site_host():
    hot = HotSettings()
    hot.network.proxy = _GLOBAL_PROXY
    hot.scraping.site_config[_SITE] = SiteConfig(proxy=_SITE_PROXY)

    stack = build_network_stack(hot)

    for url in _site_hosts(_SITE):
        assert stack.web_client._proxy_for(httpx.URL(url).host) == _SITE_PROXY, url
    # 未配置的 host 仍走全局代理
    assert stack.web_client._proxy_for("never-configured.example.com") == _GLOBAL_PROXY
    await stack.web_client.close()


@pytest.mark.asyncio
async def test_site_proxy_includes_configured_base_url():
    """用户配置的镜像域按站点代理发出, 否则镜像请求会走全局代理."""
    hot = HotSettings()
    hot.network.proxy = _GLOBAL_PROXY
    hot.scraping.site_config[_SITE] = SiteConfig(proxy=_SITE_PROXY, base_url="https://mirror.example")

    stack = build_network_stack(hot)

    assert stack.web_client._proxy_for("mirror.example") == _SITE_PROXY
    await stack.web_client.close()


@pytest.mark.asyncio
async def test_use_proxy_false_goes_direct():
    hot = HotSettings()
    hot.network.proxy = _GLOBAL_PROXY
    hot.scraping.site_config[_SITE] = SiteConfig(use_proxy=False, proxy=_SITE_PROXY)

    stack = build_network_stack(hot)

    for url in _site_hosts(_SITE):
        assert stack.web_client._proxy_for(httpx.URL(url).host) is None, url
    await stack.web_client.close()


@pytest.mark.asyncio
async def test_without_site_proxy_everything_uses_global():
    hot = HotSettings()
    hot.network.proxy = _GLOBAL_PROXY

    stack = build_network_stack(hot)

    for url in _site_hosts(_SITE):
        assert stack.web_client._proxy_for(httpx.URL(url).host) == _GLOBAL_PROXY, url
    await stack.web_client.close()


@pytest.mark.asyncio
async def test_browser_pool_keeps_global_proxy():
    """浏览器通道只有全局代理: 站点代理与站点关闭代理都不改变它."""
    hot = HotSettings()
    hot.network.proxy = _GLOBAL_PROXY
    hot.scraping.site_config[_SITE] = SiteConfig(use_proxy=False, proxy=_SITE_PROXY)

    stack = build_network_stack(hot)

    assert stack.browser._proxy == _GLOBAL_PROXY
    await stack.web_client.close()
    await stack.browser.close()
