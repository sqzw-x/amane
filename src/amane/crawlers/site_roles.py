"""站点角色 — 由已注册爬虫的 ``profile()`` 声明推导, 供配置 schema 与运行时校验共用.

不进 HotSettings. 双料站 = 同一 ``SiteName`` 同时出现在影片 / 演员注册表, 不要手写名单.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast, overload

from pydantic.config import JsonDict

from amane.crawlers import actor_registry, registry
from amane.enums import SiteName
from amane.plugins.models import SourceCapability, is_external_source_id

_ACTOR_PROFILE = SourceCapability.ACTOR_PROFILE
_ACTOR_IMAGE = SourceCapability.ACTOR_IMAGE


def _actor_sites(capability: SourceCapability) -> tuple[SiteName, ...]:
    """演员注册表插入序, 过滤声明了给定能力的爬虫."""
    sites: list[SiteName] = []
    for cls in actor_registry.classes():
        profile = cls.profile()
        if capability not in profile.capabilities:
            continue
        name = profile.name
        sites.append(name if isinstance(name, SiteName) else SiteName(str(name)))
    return tuple(sites)


# 档案 / 头像列表顺序 = actor_registry.register 顺序 (默认 profile_sites 优先级).
ACTOR_PROFILE_SITES: tuple[SiteName, ...] = _actor_sites(_ACTOR_PROFILE)
ACTOR_IMAGE_SITES: tuple[SiteName, ...] = _actor_sites(_ACTOR_IMAGE)

_ACTOR_SITE_SET = frozenset({*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES})
_FILM_SITE_SET = frozenset(SiteName(s) for s in registry.sites())

# 只挂演员、不进影片 registry 的站. 双料站同时在两边, 不进此集合.
ACTOR_ONLY_SITES: frozenset[SiteName] = _ACTOR_SITE_SET - _FILM_SITE_SET

# 影片元数据站 = 影片 registry; 成员序跟 SiteName 字母序, 保证 schema enum 稳定.
FILM_METADATA_SITES: tuple[SiteName, ...] = tuple(s for s in SiteName if s in _FILM_SITE_SET)

# 消费 FetchOptions.language 的影片站. 聚合引擎只对这些站展开 (site, lang) 节点.
MULTI_LANGUAGE_SITES: frozenset[SiteName] = frozenset(
    s for s in FILM_METADATA_SITES if (cls := registry.get(s.value)) is not None and cls.profile().multi_language
)
MULTI_LANGUAGE_SOURCE_IDS: frozenset[str] = frozenset(site.value for site in MULTI_LANGUAGE_SITES)

_ACTOR_PROFILE_SET = frozenset(ACTOR_PROFILE_SITES)
_ACTOR_IMAGE_SET = frozenset(ACTOR_IMAGE_SITES)


def is_actor_profile_site(site: SiteName) -> bool:
    return site in _ACTOR_PROFILE_SET


def is_actor_image_site(site: SiteName) -> bool:
    return site in _ACTOR_IMAGE_SET


def site_list_schema(sites: Sequence[SiteName], *, ordered: bool = True) -> Callable[[JsonDict], None]:
    """把 list[SiteName] 字段的 items 收窄为给定站点枚举 (供设置页只展示可选站)."""

    enum_vals = [s.value for s in sites]

    def extra(schema: JsonDict) -> None:
        schema["items"] = cast("JsonDict", {"type": "string", "enum": enum_vals})
        if ordered:
            schema["x-ordered"] = True

    return extra


def site_list_value_schema(sites: Sequence[SiteName], *, ordered: bool = True) -> dict[str, Any]:
    """dict[K, list[SiteName]] 的 additionalProperties.items 收窄用."""
    items: dict[str, Any] = {"type": "string", "enum": [s.value for s in sites]}
    out: dict[str, Any] = {"items": items}
    if ordered:
        out["x-ordered"] = True
    return out


@overload
def assert_sites_allowed(
    sites: list[str],
    allowed: frozenset[SiteName] | frozenset[str],
    *,
    field: str,
    allow_external: bool = False,
) -> list[str]: ...


@overload
def assert_sites_allowed(
    sites: list[SiteName],
    allowed: frozenset[SiteName] | frozenset[str],
    *,
    field: str,
    allow_external: bool = False,
) -> list[SiteName]: ...


def assert_sites_allowed(
    sites: list[str] | list[SiteName],
    allowed: frozenset[SiteName] | frozenset[str],
    *,
    field: str,
    allow_external: bool = False,
) -> list[str] | list[SiteName]:
    """校验站点列表 ⊆ allowed, 可选放行外部 source ID; 不合法时抛 ValueError."""
    allowed_values = {s.value if isinstance(s, SiteName) else s for s in allowed}
    values = [s.value if isinstance(s, SiteName) else s for s in sites]
    bad = [
        value
        for value in values
        if value not in allowed_values and not (allow_external and is_external_source_id(value))
    ]
    if bad:
        allowed_vals = sorted(allowed_values)
        suffix = " or namespaced source ids" if allow_external else ""
        raise ValueError(f"{field} contains sites outside allowed set {allowed_vals}{suffix}: {bad}")
    return sites
