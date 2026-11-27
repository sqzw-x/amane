"""单跳 alias + block. 表内保持规范形; apply 只查一次, 不做多跳递归."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..crawlers.models import FilmActor, MediaMetadata
from ..enums import MetadataField
from ..parsing import split_actor_aliases
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


@dataclass(frozen=True, slots=True)
class ResolvedField:
    """一次来源结果按规则收敛后的取值; 三类互斥, 只有对应字段用得上的一项."""

    value: str | None = None
    actors: list[FilmActor] | None = None
    names: list[str] | None = None

    @property
    def empty(self) -> bool:
        """没有可写入的值; 调用方据此继续沿字段链回退."""
        return not (self.value or self.actors or self.names)


def resolve_field_value(field: MetadataField, data: MediaMetadata, rules_by_kind: RulesByKind) -> ResolvedField:
    """按治理规则收敛一次来源结果的某个分类字段.

    非分类字段原样返回. 多值字段整份剔除后为空, 与站点没给值同样继续回退; 部分保留不触发回退.
    演员只按名单剔除, 性别随条目保留.
    """
    match field:
        case MetadataField.ACTORS:
            rules = rules_by_kind.get(FacetKind.ACTOR, {})
            return ResolvedField(actors=[item for item in data.actors if resolve_name(item.name, rules) is not None])
        case MetadataField.DIRECTORS:
            return ResolvedField(names=apply_list(list(data.directors), rules_by_kind.get(FacetKind.DIRECTOR, {})))
        case MetadataField.TAGS:
            return ResolvedField(names=apply_list(list(data.tags), rules_by_kind.get(FacetKind.TAG, {})))
        case MetadataField.SERIES:
            return ResolvedField(value=apply_scalar(data.series, rules_by_kind.get(FacetKind.SERIES, {})))
        case MetadataField.STUDIO:
            return ResolvedField(value=apply_scalar(data.studio, rules_by_kind.get(FacetKind.STUDIO, {})))
        case MetadataField.PUBLISHER:
            return ResolvedField(value=apply_scalar(data.publisher, rules_by_kind.get(FacetKind.PUBLISHER, {})))
        case _:
            return ResolvedField(value=getattr(data, field, None))


def empty_rules_by_kind() -> dict[FacetKind, dict[str, RuleEntry]]:
    return {kind: {} for kind in SCRAPE_FACET_KINDS}


def rule_lookup_names(values: FacetFieldValues) -> dict[FacetKind, set[str]]:
    """六个真值字段在规则表里可能命中的名字, 按 kind 分组.

    演员同时给出原始名与 ``name(alias1, alias2)`` 拆出的展示名: 落库前先按原始名判一次 block,
    再按解析出的展示名判一次, 两次都在查找范围内.
    """
    names: dict[FacetKind, set[str]] = {kind: set() for kind in SCRAPE_FACET_KINDS}
    names[FacetKind.ACTOR] = {
        candidate for raw in values.actors if raw for candidate in (raw, split_actor_aliases(raw)[0])
    }
    names[FacetKind.DIRECTOR].update(values.directors)
    names[FacetKind.TAG].update(values.tags)
    for kind, value in (
        (FacetKind.STUDIO, values.studio),
        (FacetKind.PUBLISHER, values.publisher),
        (FacetKind.SERIES, values.series),
    ):
        if value:
            names[kind].add(value)
    return {kind: found for kind, found in names.items() if found}


type RuleRow = tuple[FacetKind, str, FacetRuleAction, str | None, FacetRuleAction | None]


def rules_from_rows(rows: Iterable[RuleRow]) -> dict[FacetKind, dict[str, RuleEntry]]:
    """定向查询结果转成规则表. 每行是 (kind, source_name, action, target_name, target_action).

    target_action 是 alias 目标的 action (目标行不存在时为 None); 只取一跳, 不追踪 alias 的 alias.
    调用方须保证每行都带目标行快照 (定向读取走外连接, 全表读取在同一次结果里反查), 否则两条路径会产出不同的 ``RuleEntry``.
    """
    by_kind = empty_rules_by_kind()
    for kind, source_name, action, target_name, target_action in rows:
        if kind not in SCRAPE_FACET_KINDS:
            continue
        resolved = FacetRuleAction(action)
        if resolved == FacetRuleAction.ALIAS and target_action == FacetRuleAction.BLOCK:
            resolved = FacetRuleAction.BLOCK
        by_kind[kind][source_name] = RuleEntry(
            action=resolved, target_name=None if resolved == FacetRuleAction.BLOCK else target_name
        )
    return by_kind
