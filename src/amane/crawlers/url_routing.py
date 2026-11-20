"""按详情页网址确定内置来源.

指定网址刮削不检索番号, 网址就是详情页, 因此来源只能由地址反推: 命中来源自己声明的域名
(含用户在站点配置里改写的 ``base_url``). 未命中任何来源、或来源不提供网址解析时返回 ``None``.
插件来源不参与: 其 provider 只有按 ``SearchQuery`` 检索的接口.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from .registry import registry
from .site_roles import FILM_METADATA_SITES

if TYPE_CHECKING:
    from .base import Crawler


class SiteAddress(Protocol):
    """站点配置中决定来源域名的那一项."""

    @property
    def base_url(self) -> str | None: ...


def _host(url: str) -> str | None:
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.rstrip(".").removeprefix("www.").lower()


def _candidate_hosts(cls: type[Crawler], configured: SiteAddress | None) -> list[tuple[int, str]]:
    """来源声明的域名与优先级. 数字越小越优先: 用户改写 > 来源自带入口 > 其它备用地址."""
    profile = cls.profile()
    if not profile.url_scrape:
        return []
    hosts: list[tuple[int, str]] = []
    if configured is not None and configured.base_url:
        hosts.append((0, configured.base_url))
    if profile.base_url:
        hosts.append((1, profile.base_url))
    hosts.extend((2, url) for url in profile.urls)
    return hosts


def resolve_url_source(url: str, site_configs: Mapping[str, SiteAddress] | None = None) -> str | None:
    """返回网址所属的内置来源 ID; 无法确定时返回 ``None``.

    用户改写的 ``base_url`` 优先于来源自带的备用地址, 因此同一域名被多个来源声明时, 被显式配置
    成入口的那个来源胜出.
    """
    host = _host(url)
    if host is None:
        return None
    best: tuple[int, int] | None = None
    source: str | None = None
    for order, site in enumerate(FILM_METADATA_SITES):
        cls = registry.get(site)
        if cls is None:
            continue
        configured = site_configs.get(str(site)) if site_configs is not None else None
        for rank, candidate in _candidate_hosts(cls, configured):
            if _host(candidate) != host:
                continue
            key = (rank, order)
            if best is None or key < best:
                best, source = key, str(site)
            break
    return source
