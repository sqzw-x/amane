"""站点角色常量 - 配置 schema 暴露与运行时校验共用 (不进 HotSettings).

内部仍用统一 ``SiteName``; 各配置列表只允许具备对应角色的子集.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast, overload

from pydantic.config import JsonDict

from amane.enums import SiteName
from amane.plugins.models import is_external_source_id

# 演员档案站 (标量人物字段 / 简介等). 双料站兼影片; theporndb 垫后 (无 token 空跑).
ACTOR_PROFILE_SITES: tuple[SiteName, ...] = (
    SiteName.MINNANO,
    SiteName.JAVDB,
    SiteName.WIKIPEDIA,
    SiteName.THEPORNDB,
)

# 演员头像站
ACTOR_IMAGE_SITES: tuple[SiteName, ...] = (SiteName.GFRIENDS,)

# 双料站同时出现在 FILM_METADATA_SITES 与 ACTOR_PROFILE_SITES, 不进 ACTOR_ONLY_SITES.
_DUAL_ROLE_SITES: frozenset[SiteName] = frozenset({SiteName.JAVDB, SiteName.THEPORNDB})

ACTOR_ONLY_SITES: frozenset[SiteName] = frozenset({*ACTOR_PROFILE_SITES, *ACTOR_IMAGE_SITES}) - _DUAL_ROLE_SITES

# 影片元数据站 = SiteName 全集减去演员专用 (与 film registry 对齐, 见 test_enum_consistency)
FILM_METADATA_SITES: tuple[SiteName, ...] = tuple(s for s in SiteName if s not in ACTOR_ONLY_SITES)

# 爬虫会消费 FetchOptions.language 的站点. 聚合引擎只对这些站展开 (site, lang) 节点.
MULTI_LANGUAGE_SITES: frozenset[SiteName] = frozenset({SiteName.IQQTV, SiteName.R18DEV})
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
