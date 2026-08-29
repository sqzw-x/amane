"""演员多源聚合 - 按站点顺序标量填空, 头像源优先拼接 image_urls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from amane.crawlers.actor import ActorMetadata
from amane.enums import ActorGender, SiteName

if TYPE_CHECKING:
    from collections.abc import Mapping


# 标量人物字段: 先到先得 (空才填). source_urls 为多源字典, 不在此列.
_SCALAR_FIELDS: tuple[str, ...] = (
    "gender",
    "birthday",
    "birthplace",
    "height",
    "bust",
    "waist",
    "hip",
    "cup",
    "overview",
    "tagline",
)


class AggregatedActor(BaseModel):
    """多源合并后的演员元数据."""

    aliases: list[str] = Field(default_factory=list)
    gender: ActorGender = ActorGender.UNKNOWN
    birthday: str | None = None
    birthplace: str | None = None
    height: int | None = None
    bust: int | None = None
    waist: int | None = None
    hip: int | None = None
    cup: str | None = None
    overview: str | None = None
    tagline: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    source_urls: dict[str, str] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, dict[str, object]] = Field(default_factory=dict)


def _scalar_empty(field: str, value: object) -> bool:
    """标量空位: gender 的 unknown 视为可被填空覆盖."""
    if field == "gender":
        return value in (None, "", ActorGender.UNKNOWN)
    return value in (None, "")


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def merge_actor_metadata(
    results: Mapping[SiteName, ActorMetadata | None], *, profile_sites: list[SiteName], image_sites: list[SiteName]
) -> AggregatedActor:
    """
    按站点顺序合并演员结果.

    - 标量: profile_sites 顺序填空, 记入 field_sources
    - aliases: 各站 ``name`` + ``aliases`` 并集 (先到先得). 站点显示名不当身份.
    - provider_ids / source_urls: 并集 (先到先得)
    - image_urls: image_sites 优先, 再档案站附图, 去重保序
    - raw: 有结果的站点整份快照
    """
    out = AggregatedActor()

    for site in profile_sites:
        meta = results.get(site)
        if meta is None:
            continue
        out.raw[site] = meta.model_dump(exclude_none=False)
        for field in _SCALAR_FIELDS:
            current = getattr(out, field)
            value = getattr(meta, field)
            if _scalar_empty(field, current) and not _scalar_empty(field, value):
                setattr(out, field, value)
                out.field_sources[field] = site
        if meta.name or meta.aliases:
            out.aliases = _dedupe_preserve([*out.aliases, *([meta.name] if meta.name else []), *meta.aliases])
        if meta.provider_ids:
            for k, v in meta.provider_ids.items():
                out.provider_ids.setdefault(k, v)
        if meta.source_url:
            out.source_urls.setdefault(site, meta.source_url)

    image_order = [*image_sites, *[s for s in profile_sites if s not in image_sites]]
    images: list[str] = []
    for site in image_order:
        meta = results.get(site)
        if meta is None:
            continue
        if site not in out.raw:
            out.raw[site] = meta.model_dump(exclude_none=False)
        if meta.source_url:
            out.source_urls.setdefault(site, meta.source_url)
        if meta.image_urls:
            images.extend(meta.image_urls)
    out.image_urls = _dedupe_preserve(images)
    if out.image_urls and "image_urls" not in out.field_sources:
        for site in image_order:
            meta = results.get(site)
            if meta and meta.image_urls:
                out.field_sources["image_urls"] = site
                break

    return out


def merge_actor_rows_fill_empty(target: AggregatedActor, source: AggregatedActor) -> AggregatedActor:
    """将 source 填入 target 空位 (Actor 实体 merge 用)."""
    for field in _SCALAR_FIELDS:
        if _scalar_empty(field, getattr(target, field)) and not _scalar_empty(field, getattr(source, field)):
            setattr(target, field, getattr(source, field))
            src = source.field_sources.get(field)
            if src:
                target.field_sources.setdefault(field, src)
    target.aliases = _dedupe_preserve([*target.aliases, *source.aliases])
    target.image_urls = _dedupe_preserve([*target.image_urls, *source.image_urls])
    for k, v in source.provider_ids.items():
        target.provider_ids.setdefault(k, v)
    for k, v in source.source_urls.items():
        target.source_urls.setdefault(k, v)
    for site, payload in source.raw.items():
        target.raw.setdefault(site, payload)
    for field, site in source.field_sources.items():
        target.field_sources.setdefault(field, site)
    return target
