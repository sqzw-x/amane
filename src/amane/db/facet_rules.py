"""单跳 alias + block. 表内保持规范形; apply 只查一次, 不做多跳递归."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import SCRAPE_FACET_KINDS, FacetKind, FacetRuleAction, Metadata


@dataclass(frozen=True, slots=True)
class RuleEntry:
    action: FacetRuleAction
    target_name: str | None = None


type RulesBySource = Mapping[str, RuleEntry]
type RulesByKind = Mapping[FacetKind, RulesBySource]

# 真值字段对应的六个爬取侧分类; ``FacetFieldValues.same_except`` 按此集合逐项比较.
FACET_VALUE_KINDS: tuple[FacetKind, ...] = (
    FacetKind.ACTOR,
    FacetKind.DIRECTOR,
    FacetKind.TAG,
    FacetKind.STUDIO,
    FacetKind.PUBLISHER,
    FacetKind.SERIES,
)


def resolve_name(name: str, rules: RulesBySource) -> str | None:
    """单跳解析: 未命中原样返回; alias 换 target; block 或 alias 终态被 block 则 None."""
    rule = rules.get(name)
    if rule is None:
        return name
    if rule.action == FacetRuleAction.BLOCK:
        return None
    target = rule.target_name
    if target is None:
        return None
    target_rule = rules.get(target)
    if target_rule is not None and target_rule.action == FacetRuleAction.BLOCK:
        return None
    return target


def apply_list(names: list[str], rules: RulesBySource) -> list[str]:
    """保序去重; 空串跳过."""
    out: list[str] = []
    for name in names:
        if not name:
            continue
        resolved = resolve_name(name, rules)
        if resolved is None:
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def apply_scalar(value: str | None, rules: RulesBySource) -> str | None:
    """空或 block → None."""
    if value is None or not value:
        return None
    return resolve_name(value, rules)


@dataclass(frozen=True, slots=True)
class FacetFieldValues:
    """六个爬取侧分类真值字段的取值.

    批量治理 (改名 / 合并 / 删除) 在 ORM 之外变换这份值, 避免逐行读取 ``Metadata.raw`` 等宽列.
    """

    actors: list[str]
    directors: list[str]
    tags: list[str]
    studio: str | None = None
    publisher: str | None = None
    series: str | None = None

    def names(self, kind: FacetKind) -> list[str]:
        """取某一分类的名字列表; 标量分类返回至多一个元素的列表."""
        match kind:
            case FacetKind.ACTOR:
                return self.actors
            case FacetKind.DIRECTOR:
                return self.directors
            case FacetKind.TAG:
                return self.tags
            case FacetKind.STUDIO:
                return [self.studio] if self.studio else []
            case FacetKind.PUBLISHER:
                return [self.publisher] if self.publisher else []
            case FacetKind.SERIES:
                return [self.series] if self.series else []
            case _:
                raise ValueError(f"facet kind {kind} 不是爬取侧分类")

    def with_names(self, kind: FacetKind, names: list[str]) -> FacetFieldValues:
        """替换某一分类的取值; 标量分类取列表首个元素."""
        scalar = names[0] if names else None
        return FacetFieldValues(
            actors=names if kind == FacetKind.ACTOR else self.actors,
            directors=names if kind == FacetKind.DIRECTOR else self.directors,
            tags=names if kind == FacetKind.TAG else self.tags,
            studio=scalar if kind == FacetKind.STUDIO else self.studio,
            publisher=scalar if kind == FacetKind.PUBLISHER else self.publisher,
            series=scalar if kind == FacetKind.SERIES else self.series,
        )

    def same_except(self, kind: FacetKind, other: FacetFieldValues) -> bool:
        """除 ``kind`` 外的五个字段是否完全相同."""
        return all(
            other_kind == kind or self.names(other_kind) == other.names(other_kind) for other_kind in FACET_VALUE_KINDS
        )

    def apply_rules(self, rules_by_kind: RulesByKind) -> FacetFieldValues:
        """按治理规则改写六个字段; 未登记的 kind 视为无规则."""
        return FacetFieldValues(
            actors=apply_list(self.actors, rules_by_kind.get(FacetKind.ACTOR, {})),
            directors=apply_list(self.directors, rules_by_kind.get(FacetKind.DIRECTOR, {})),
            tags=apply_list(self.tags, rules_by_kind.get(FacetKind.TAG, {})),
            studio=apply_scalar(self.studio, rules_by_kind.get(FacetKind.STUDIO, {})),
            publisher=apply_scalar(self.publisher, rules_by_kind.get(FacetKind.PUBLISHER, {})),
            series=apply_scalar(self.series, rules_by_kind.get(FacetKind.SERIES, {})),
        )


def facet_field_values(meta: Metadata) -> FacetFieldValues:
    return FacetFieldValues(
        actors=list(meta.actors) if meta.actors else [],
        directors=list(meta.directors) if meta.directors else [],
        tags=list(meta.tags) if meta.tags else [],
        studio=meta.studio,
        publisher=meta.publisher,
        series=meta.series,
    )


def write_facet_field_values(meta: Metadata, values: FacetFieldValues) -> None:
    meta.actors = values.actors
    meta.directors = values.directors
    meta.tags = values.tags
    meta.studio = values.studio
    meta.publisher = values.publisher
    meta.series = values.series


def apply_metadata_facet_fields(meta: Metadata, rules_by_kind: RulesByKind) -> None:
    """就地改写六个爬取侧分类真值字段."""
    write_facet_field_values(meta, facet_field_values(meta).apply_rules(rules_by_kind))


def empty_rules_by_kind() -> dict[FacetKind, dict[str, RuleEntry]]:
    return {kind: {} for kind in SCRAPE_FACET_KINDS}
