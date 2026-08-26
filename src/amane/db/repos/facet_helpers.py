"""Facet 同步 / 查询辅助 (MetadataRepoMixin / FacetsRepoMixin 共用).

无前缀名为 mixin 可调用的包内公开 API; ``_`` 前缀为本模块私有实现.
不经 ``amane.db.repos`` 包导出面暴露 (见 ``__init__.py``).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete as sqla_delete
from sqlalchemy import or_, update
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.functions import count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ...parsing import split_actor_aliases
from ..actor_lookup import list_actor_aliases, resolve_actor_by_name
from ..actor_person import merge_person_fields_into_target
from ..facet_rules import RuleEntry, apply_metadata_facet_fields, empty_rules_by_kind
from ..models import (
    SCRAPE_FACET_KINDS,
    Actor,
    ActorAlias,
    Comment,
    Director,
    FacetKind,
    FacetRule,
    FacetRuleAction,
    FacetSortField,
    MediaFile,
    MediaFileStatus,
    Metadata,
    MetadataActor,
    MetadataDirector,
    MetadataTag,
    MetadataUserTag,
    Publisher,
    Series,
    SortOrder,
    Studio,
    Tag,
    UserTag,
)
from ..repo_types import FacetItem, _facet_primary_order, _utcnow

# ==================== Facet sync / query helpers ====================

# 关联表投影: Metadata list JSON 为真值 (actor/director/tag), 或纯挂载 (user_tag).
# 标量投影: Metadata.studio/publisher/series 字符串为真值, 实体表按 name 对齐.


type _NamedEntity = Actor | Director | Tag | UserTag | Studio | Publisher | Series


@dataclass(frozen=True, slots=True)
class _LinkFacetSpec:
    kind: FacetKind
    entity: type[Actor] | type[Director] | type[Tag] | type[UserTag]
    link: type[MetadataActor] | type[MetadataDirector] | type[MetadataTag] | type[MetadataUserTag]
    link_fk: Any
    label: str
    get_names: Callable[[Metadata], list[str]] | None = None
    set_names: Callable[[Metadata, list[str]], None] | None = None
    # 搜索时一并命中的别名模型 (仅 Actor)
    alias_model: type[ActorAlias] | None = None


@dataclass(frozen=True, slots=True)
class _ScalarFacetSpec:
    kind: FacetKind
    entity: type[Studio] | type[Publisher] | type[Series]
    meta_col: Mapped
    label: str


def _get_actors(m: Metadata) -> list[str]:
    return m.actors


def _set_actors(m: Metadata, names: list[str]) -> None:
    m.actors = names


def _get_directors(m: Metadata) -> list[str]:
    return m.directors


def _set_directors(m: Metadata, names: list[str]) -> None:
    m.directors = names


def _get_tags(m: Metadata) -> list[str]:
    return m.tags


def _set_tags(m: Metadata, names: list[str]) -> None:
    m.tags = names


LINK_FACETS: dict[FacetKind, _LinkFacetSpec] = {
    FacetKind.ACTOR: _LinkFacetSpec(
        kind=FacetKind.ACTOR,
        entity=Actor,
        link=MetadataActor,
        link_fk=MetadataActor.actor_id,
        label="演员",
        get_names=_get_actors,
        set_names=_set_actors,
        alias_model=ActorAlias,
    ),
    FacetKind.DIRECTOR: _LinkFacetSpec(
        kind=FacetKind.DIRECTOR,
        entity=Director,
        link=MetadataDirector,
        link_fk=MetadataDirector.director_id,
        label="导演",
        get_names=_get_directors,
        set_names=_set_directors,
    ),
    FacetKind.TAG: _LinkFacetSpec(
        kind=FacetKind.TAG,
        entity=Tag,
        link=MetadataTag,
        link_fk=MetadataTag.tag_id,
        label="标签",
        get_names=_get_tags,
        set_names=_set_tags,
    ),
    FacetKind.USER_TAG: _LinkFacetSpec(
        kind=FacetKind.USER_TAG,
        entity=UserTag,
        link=MetadataUserTag,
        link_fk=MetadataUserTag.user_tag_id,
        label="标签",
    ),
}

SCALAR_FACETS: dict[FacetKind, _ScalarFacetSpec] = {
    FacetKind.STUDIO: _ScalarFacetSpec(
        kind=FacetKind.STUDIO,
        entity=Studio,
        meta_col=col(Metadata.studio),
        label="厂商",
    ),
    FacetKind.PUBLISHER: _ScalarFacetSpec(
        kind=FacetKind.PUBLISHER,
        entity=Publisher,
        meta_col=col(Metadata.publisher),
        label="发行商",
    ),
    FacetKind.SERIES: _ScalarFacetSpec(
        kind=FacetKind.SERIES,
        entity=Series,
        meta_col=col(Metadata.series),
        label="系列",
    ),
}


async def _get_or_create_named[T: _NamedEntity](session: AsyncSession, model: type[T], name: str) -> T:
    existing = (await session.exec(select(model).where(model.name == name))).first()
    if existing is not None:
        return existing
    obj = model(name=name)
    session.add(obj)
    await session.flush()
    return obj


async def _load_rules_by_kind(session: AsyncSession) -> dict[FacetKind, dict[str, RuleEntry]]:
    out = empty_rules_by_kind()
    rows = list((await session.exec(select(FacetRule))).all())
    for row in rows:
        kind = row.kind if isinstance(row.kind, FacetKind) else FacetKind(row.kind)
        if kind not in SCRAPE_FACET_KINDS:
            continue
        action = row.action if isinstance(row.action, FacetRuleAction) else FacetRuleAction(row.action)
        out[kind][row.source_name] = RuleEntry(action=action, target_name=row.target_name)
    return out


async def apply_facet_rules_to_metadata(session: AsyncSession, meta: Metadata) -> None:
    rules = await _load_rules_by_kind(session)
    apply_metadata_facet_fields(meta, rules)
    session.add(meta)
    await session.flush()


async def _get_facet_rule(session: AsyncSession, kind: FacetKind, source_name: str) -> FacetRule | None:
    return (
        await session.exec(
            select(FacetRule).where(col(FacetRule.kind) == kind, col(FacetRule.source_name) == source_name)
        )
    ).first()


async def _set_facet_rule(
    session: AsyncSession,
    kind: FacetKind,
    source_name: str,
    action: FacetRuleAction,
    target_name: str | None,
) -> FacetRule:
    """规则行唯一写入点; actor 的 alias 规则已退役, 在此层直接拒绝."""
    if kind == FacetKind.ACTOR and action == FacetRuleAction.ALIAS:
        raise ValueError("演员别名规则已由 actor_aliases 表取代")
    existing = await _get_facet_rule(session, kind, source_name)
    now = _utcnow()
    if existing is None:
        rule = FacetRule(
            kind=kind,
            source_name=source_name,
            action=action,
            target_name=target_name,
            created_at=now,
            updated_at=now,
        )
        session.add(rule)
        await session.flush()
        return rule
    existing.action = action
    existing.target_name = target_name
    existing.updated_at = now
    session.add(existing)
    await session.flush()
    return existing


async def _upsert_alias(session: AsyncSession, kind: FacetKind, source: str, target: str) -> None:
    """写入单跳 alias, 并压缩入边; 目标已被 block 时改为 block source.

    仅限非 actor kind — actor 别名已由 ``ActorAlias`` 表承担 (见
    ``actor_swap_display_name`` / ``merge_link_facets``).
    """
    if kind == FacetKind.ACTOR:
        raise ValueError("演员别名规则已由 actor_aliases 表取代")
    if kind not in SCRAPE_FACET_KINDS:
        raise ValueError(f"facet kind {kind.value} 不支持别名规则")
    if source == target:
        raise ValueError("别名源与目标不能相同")

    target_rule = await _get_facet_rule(session, kind, target)
    if target_rule is not None and target_rule.action == FacetRuleAction.BLOCK:
        await _upsert_block(session, kind, source)
        return

    final_target = target
    if target_rule is not None and target_rule.action == FacetRuleAction.ALIAS:
        if target_rule.target_name is None:
            await _upsert_block(session, kind, source)
            return
        final_target = target_rule.target_name

    if source == final_target:
        existing = await _get_facet_rule(session, kind, source)
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        return

    await _set_facet_rule(session, kind, source, FacetRuleAction.ALIAS, final_target)

    inbound = list(
        (
            await session.exec(
                select(FacetRule).where(
                    col(FacetRule.kind) == kind,
                    col(FacetRule.action) == FacetRuleAction.ALIAS,
                    col(FacetRule.target_name) == source,
                )
            )
        ).all()
    )
    for rule in inbound:
        if rule.source_name == final_target:
            await session.delete(rule)
        else:
            rule.target_name = final_target
            rule.updated_at = _utcnow()
            session.add(rule)
    await session.flush()


async def _upsert_block(session: AsyncSession, kind: FacetKind, name: str) -> set[str]:
    """写 block 并将指向 name 的 alias 压成 block; 返回全部变为 block 的名字."""
    if kind not in SCRAPE_FACET_KINDS:
        raise ValueError(f"facet kind {kind.value} 不支持黑名单规则")
    blocked: set[str] = {name}
    await _set_facet_rule(session, kind, name, FacetRuleAction.BLOCK, None)

    inbound = list(
        (
            await session.exec(
                select(FacetRule).where(
                    col(FacetRule.kind) == kind,
                    col(FacetRule.action) == FacetRuleAction.ALIAS,
                    col(FacetRule.target_name) == name,
                )
            )
        ).all()
    )
    for rule in inbound:
        blocked.add(rule.source_name)
        rule.action = FacetRuleAction.BLOCK
        rule.target_name = None
        rule.updated_at = _utcnow()
        session.add(rule)
    await session.flush()
    return blocked


async def _strip_names_from_link_metadata(session: AsyncSession, spec: _LinkFacetSpec, names: set[str]) -> None:
    if not names or spec.get_names is None or spec.set_names is None:
        return
    entity_ids: list[int] = []
    for name in names:
        entity = (await session.exec(select(spec.entity).where(spec.entity.name == name))).first()
        if entity is not None and entity.id is not None:
            entity_ids.append(entity.id)
    if not entity_ids:
        return
    stmt = select(Metadata).where(
        col(Metadata.id).in_(select(spec.link.metadata_id).where(col(spec.link_fk).in_(entity_ids)))
    )
    metadatas = list((await session.exec(stmt)).all())
    for meta in metadatas:
        current = spec.get_names(meta)
        stripped = [n for n in current if n not in names]
        if stripped == current:
            continue
        spec.set_names(meta, stripped)
        meta.updated_at = _utcnow()
        session.add(meta)
        await session.flush()
        await sync_metadata_facets(session, meta)


async def _strip_names_from_scalar_metadata(session: AsyncSession, kind: FacetKind, names: set[str]) -> None:
    if not names:
        return
    now = _utcnow()
    if kind == FacetKind.STUDIO:
        await session.exec(
            update(Metadata).where(col(Metadata.studio).in_(list(names))).values(studio=None, updated_at=now)
        )
    elif kind == FacetKind.PUBLISHER:
        await session.exec(
            update(Metadata).where(col(Metadata.publisher).in_(list(names))).values(publisher=None, updated_at=now)
        )
    elif kind == FacetKind.SERIES:
        await session.exec(
            update(Metadata).where(col(Metadata.series).in_(list(names))).values(series=None, updated_at=now)
        )
    else:
        raise ValueError(f"not a scalar facet kind: {kind}")
    await session.flush()


async def _actor_delete_block_names(session: AsyncSession, actor: Actor) -> set[str]:
    """删除演员时拉黑的名字: 展示名 + 未被其它演员引用的别名行.

    共享别名 (其它演员的别名行/展示名也在用) 不拉黑, 避免误伤仍有效的身份;
    实体删除后其独有名行随级联消失, 不拉黑则重刮会把名字复活为新实体.
    """
    assert actor.id is not None
    blocked: set[str] = {actor.name}
    alias_names = set((await session.exec(select(ActorAlias.name).where(col(ActorAlias.actor_id) == actor.id))).all())
    if not alias_names:
        return blocked
    other_display = set(
        (
            await session.exec(select(Actor.name).where(col(Actor.name).in_(alias_names), col(Actor.id) != actor.id))
        ).all()
    )
    other_rows = set(
        (
            await session.exec(
                select(ActorAlias.name).where(
                    col(ActorAlias.name).in_(alias_names), col(ActorAlias.actor_id) != actor.id
                )
            )
        ).all()
    )
    for name in alias_names - other_display - other_rows:
        blocked.add(name)
    return blocked


async def delete_link_facet(session: AsyncSession, spec: _LinkFacetSpec, facet_id: int) -> bool:
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return False
    if spec.kind in SCRAPE_FACET_KINDS:
        if spec.kind == FacetKind.ACTOR:
            assert isinstance(entity, Actor)
            blocked = await _actor_delete_block_names(session, entity)
            for name in blocked:
                await _upsert_block(session, spec.kind, name)
        else:
            blocked = await _upsert_block(session, spec.kind, entity.name)
        await _strip_names_from_link_metadata(session, spec, blocked)
    await session.exec(sqla_delete(spec.link).where(col(spec.link_fk) == facet_id))
    if spec.kind == FacetKind.ACTOR:
        # 显式删别名行, 不依赖 FK pragma (与项目既有清理约定一致).
        await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == facet_id))
    await session.delete(entity)
    await session.commit()
    return True


async def delete_scalar_facet(session: AsyncSession, spec: _ScalarFacetSpec, facet_id: int) -> bool:
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return False
    blocked = await _upsert_block(session, spec.kind, entity.name)
    await _strip_names_from_scalar_metadata(session, spec.kind, blocked)
    await session.delete(entity)
    await session.commit()
    return True


def normalize_names(names: list[str] | None) -> list[str]:
    if not names:
        return []
    return [n for n in names if isinstance(n, str) and n]


def unique_ids(ids: Sequence[int] | None) -> list[int]:
    """保序去重的正整数 id 列表; None/空 → []."""
    if not ids:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


async def resolve_scalar_facet_names(
    session: AsyncSession, model: type[Studio] | type[Publisher] | type[Series], ids: Sequence[int] | None
) -> list[str]:
    """按 id 解析标量分类实体名; 未知 id 跳过."""
    names: list[str] = []
    for facet_id in unique_ids(ids):
        entity = await session.get(model, facet_id)
        if entity is not None:
            names.append(entity.name)
    return names


def _replace_names_in_list(values: list[str], old_names: set[str], new_name: str) -> list[str]:
    """将 list 中命中 old_names 的元素替换为 new_name, 保序去重.

    合并场景下 old_names 可包含多个来源名, 统一替换为同一 target 名后去重.
    """
    result: list[str] = []
    for v in values:
        replaced = new_name if v in old_names else v
        if replaced not in result:
            result.append(replaced)
    return result


async def cascade_delete_metadata(session: AsyncSession, metadata: Metadata) -> None:
    """删除单条 Metadata 的应用层级联清理 (不提交事务, 供单条/批量删除共用).

    MediaFile.metadata_id 可空, 删 Metadata 时必须 nullify 并回 PENDING, 否则留下悬空 FK;
    文件记录本身保留, 可再次刮削. 分类关联 / 评论 / 用户 tag 挂载显式清理 (不依赖 SQLite FK
    pragma); Actor/Director 等目录实体本身保留.
    """
    assert metadata.id is not None
    metadata_id = metadata.id
    stmt = select(MediaFile).where(MediaFile.metadata_id == metadata_id)
    result = await session.exec(stmt)
    for mf in result.all():
        mf.metadata_id = None
        mf.status = MediaFileStatus.PENDING
        mf.updated_at = _utcnow()
        session.add(mf)
    await session.exec(sqla_delete(MetadataActor).where(col(MetadataActor.metadata_id) == metadata_id))
    await session.exec(sqla_delete(MetadataDirector).where(col(MetadataDirector.metadata_id) == metadata_id))
    await session.exec(sqla_delete(MetadataTag).where(col(MetadataTag.metadata_id) == metadata_id))
    await session.exec(sqla_delete(MetadataUserTag).where(col(MetadataUserTag.metadata_id) == metadata_id))
    await session.exec(sqla_delete(Comment).where(col(Comment.metadata_id) == metadata_id))
    await session.delete(metadata)


# ==================== ActorAlias 行写入 ====================
#
# 别名以行为单位落库: 展示名不入表 (Actor.name 全局唯一, 表内 (actor_id, name) 唯一),
# 空名/与展示名相同的项丢弃, 幂等可重复调用.


def _normalize_alias_names(names: Sequence[str], display_name: str) -> list[str]:
    out: list[str] = []
    for n in names:
        n = (n or "").strip()
        if not n or n == display_name or n in out:
            continue
        out.append(n)
    return out


async def _max_alias_position(session: AsyncSession, actor_id: int) -> int:
    last = (
        await session.exec(
            select(ActorAlias.position)
            .where(col(ActorAlias.actor_id) == actor_id)
            .order_by(col(ActorAlias.position).desc())
            .limit(1)
        )
    ).first()
    return int(last) if last is not None else -1


async def add_actor_aliases(session: AsyncSession, actor: Actor, names: Sequence[str]) -> None:
    """追加别名行 (去重去空, 跳过展示名); 无新行时不做任何写."""
    assert actor.id is not None
    candidates = _normalize_alias_names(names, actor.name)
    if not candidates:
        return
    existing = set((await session.exec(select(ActorAlias.name).where(col(ActorAlias.actor_id) == actor.id))).all())
    missing = [n for n in candidates if n not in existing]
    if not missing:
        return
    position = await _max_alias_position(session, actor.id)
    now = _utcnow()
    for name in missing:
        position += 1
        session.add(ActorAlias(actor_id=actor.id, name=name, position=position, created_at=now, updated_at=now))
    await session.flush()


async def replace_actor_aliases(session: AsyncSession, actor: Actor, names: Sequence[str]) -> None:
    """整表替换演员别名行 (保序写入 position); 仅换别名, 不动 person 字段."""
    assert actor.id is not None
    await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == actor.id))
    await session.flush()
    await add_actor_aliases(session, actor, names)


async def add_one_actor_alias(session: AsyncSession, actor: Actor, name: str) -> bool:
    """幂等追加单个别名行; 空名 / 与展示名相同 / 已存在 → False."""
    candidates = _normalize_alias_names([name], actor.name)
    if not candidates:
        return False
    existing = set((await session.exec(select(ActorAlias.name).where(col(ActorAlias.actor_id) == actor.id))).all())
    if candidates[0] in existing:
        return False
    await add_actor_aliases(session, actor, [name])
    return True


async def remove_one_actor_alias(session: AsyncSession, actor_id: int, name: str) -> bool:
    """删除单个别名行; 名字为空 / 行不存在 → False. 展示名本身不在行内."""
    name = (name or "").strip()
    row = (
        await session.exec(
            select(ActorAlias.id).where(col(ActorAlias.actor_id) == actor_id, col(ActorAlias.name) == name)
        )
    ).first()
    if row is None:
        return False
    await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.id) == row))
    await session.flush()
    return True


async def swap_actor_display_name(session: AsyncSession, actor: Actor, old_name: str, new_name: str) -> None:
    """展示名切换的别名行交换: 新名行出表 (即将成为展示名), 旧展示名入表 (追加末尾).

    只动别名行, 不改 ``Actor.name`` (由调用方完成); 调用时机在该名字尚未修改时.
    """
    assert actor.id is not None
    await session.exec(
        sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == actor.id, col(ActorAlias.name) == new_name)
    )
    exists = (
        await session.exec(
            select(ActorAlias.id).where(col(ActorAlias.actor_id) == actor.id, col(ActorAlias.name) == old_name)
        )
    ).first()
    if exists is None:
        position = (await _max_alias_position(session, actor.id)) + 1
        now = _utcnow()
        session.add(ActorAlias(actor_id=actor.id, name=old_name, position=position, created_at=now, updated_at=now))
    await session.flush()


async def move_actor_alias_rows(session: AsyncSession, target: Actor, sources: Sequence[Actor]) -> None:
    """merge 用: 源演员的展示名与别名行并入 target 别名行.

    保持已有行在前, 源展示名/源别名行依次追加; target 展示名不入表.
    源实体随后删除, 其残留行随 FK 级联消失.
    """
    names: list[str] = list(await list_actor_aliases(session, target.id or 0))
    for src in sources:
        names.append(src.name)
        names.extend(await list_actor_aliases(session, src.id or 0))
    await replace_actor_aliases(session, target, names)


def _is_blocked(rules: Mapping[str, RuleEntry], name: str) -> bool:
    """actor block 规则判定 (别名规则已退役, 表中只有 block 行)."""
    rule = rules.get(name)
    return rule is not None and rule.action == FacetRuleAction.BLOCK


async def clean_actor_names(session: AsyncSession, meta: Metadata) -> None:
    """清洗 ``Metadata.actors`` 的 ``name(alias1, alias2)`` 形式 (含裸名解析).

    展示名留真值列表 (NFO、路径模板、投影都消费它), 别名并入对应演员的
    ``ActorAlias`` 行. 名字先经 ``resolve_actor_by_name`` 解析 (展示名 → 别名唯一命中
    → 歧义/无命中以名字本身为展示名), 因此站点给的任何名字都会折到已认定的演员.
    block 判定: 解析前查原始名, 解析后查解析出的展示名 (block 对别名/展示名都生效,
    与旧规则链 "B→D 且 block D 则 B 也拦" 等价). 必须在 ``apply_facet_rules_to_metadata``
    之前运行. 幂等: 无括号且名字不变时不做任何写.
    """
    raw_names = normalize_names(meta.actors)
    if not raw_names:
        return
    rules = await _load_rules_by_kind(session)
    actor_rules = rules.get(FacetKind.ACTOR, {})
    seen: set[str] = set()
    canonical: list[str] = []
    alias_targets: dict[int, list[str]] = {}
    for raw in raw_names:
        name, aliases = split_actor_aliases(raw)
        if not name or _is_blocked(actor_rules, name):
            continue
        actor = await resolve_actor_by_name(session, name)
        assert actor is not None
        if _is_blocked(actor_rules, actor.name):
            continue
        if actor.name not in seen:
            seen.add(actor.name)
            canonical.append(actor.name)
        if aliases:
            assert actor.id is not None
            alias_targets.setdefault(actor.id, []).extend(aliases)
    for actor_id, aliases in alias_targets.items():
        actor = await session.get(Actor, actor_id)
        if actor is not None:
            await add_actor_aliases(session, actor, aliases)
    if canonical == meta.actors:
        return
    meta.actors = canonical
    session.add(meta)
    await session.flush()


async def sync_metadata_facets(session: AsyncSession, meta: Metadata) -> None:
    """按 Metadata JSON/标量列重建分类投影. 不触碰 UserTag / Comment."""
    assert meta.id is not None
    metadata_id = meta.id

    await session.exec(sqla_delete(MetadataActor).where(col(MetadataActor.metadata_id) == metadata_id))
    for position, name in enumerate(normalize_names(meta.actors)):
        actor = await _get_or_create_named(session, Actor, name)
        assert actor.id is not None
        session.add(MetadataActor(metadata_id=metadata_id, actor_id=actor.id, position=position))

    await session.exec(sqla_delete(MetadataDirector).where(col(MetadataDirector.metadata_id) == metadata_id))
    for position, name in enumerate(normalize_names(meta.directors)):
        director = await _get_or_create_named(session, Director, name)
        assert director.id is not None
        session.add(MetadataDirector(metadata_id=metadata_id, director_id=director.id, position=position))

    await session.exec(sqla_delete(MetadataTag).where(col(MetadataTag.metadata_id) == metadata_id))
    for position, name in enumerate(normalize_names(meta.tags)):
        tag = await _get_or_create_named(session, Tag, name)
        assert tag.id is not None
        session.add(MetadataTag(metadata_id=metadata_id, tag_id=tag.id, position=position))

    if meta.studio:
        await _get_or_create_named(session, Studio, meta.studio)
    if meta.publisher:
        await _get_or_create_named(session, Publisher, meta.publisher)
    if meta.series:
        await _get_or_create_named(session, Series, meta.series)


async def _load_named_sources[T: _NamedEntity](
    session: AsyncSession,
    model: type[T],
    source_ids: set[int],
    label: str,
) -> list[T]:
    sources: list[T] = []
    for sid in source_ids:
        src = await session.get(model, sid)
        if src is None:
            raise ValueError(f"来源{label} {sid} 不存在")
        sources.append(src)
    return sources


async def _bulk_set_scalar_field(
    session: AsyncSession,
    kind: FacetKind,
    old_names: Sequence[str],
    new_name: str,
) -> None:
    if not old_names:
        return
    names = list(old_names)
    now = _utcnow()
    if kind == FacetKind.STUDIO:
        await session.exec(
            update(Metadata).where(col(Metadata.studio).in_(names)).values(studio=new_name, updated_at=now)
        )
    elif kind == FacetKind.PUBLISHER:
        await session.exec(
            update(Metadata).where(col(Metadata.publisher).in_(names)).values(publisher=new_name, updated_at=now)
        )
    elif kind == FacetKind.SERIES:
        await session.exec(
            update(Metadata).where(col(Metadata.series).in_(names)).values(series=new_name, updated_at=now)
        )
    else:
        raise ValueError(f"not a scalar facet kind: {kind}")


async def rename_link_facet(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    facet_id: int,
    new_name: str,
) -> FacetItem | None:
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return None
    if entity.name != new_name:
        conflict = (await session.exec(select(spec.entity).where(spec.entity.name == new_name))).first()
        if conflict is not None:
            raise ValueError("名称已存在, 请使用合并")
        old_name = entity.name
        if spec.kind == FacetKind.ACTOR:
            assert isinstance(entity, Actor)
            await swap_actor_display_name(session, entity, old_name, new_name)
        elif spec.kind in SCRAPE_FACET_KINDS:
            await _upsert_alias(session, spec.kind, old_name, new_name)
        entity.name = new_name
        entity.updated_at = _utcnow()
        session.add(entity)
        await session.flush()
        if spec.get_names is not None and spec.set_names is not None:
            stmt = select(Metadata).where(
                col(Metadata.id).in_(select(spec.link.metadata_id).where(spec.link_fk == facet_id))
            )
            metadatas = list((await session.exec(stmt)).all())
            for meta in metadatas:
                spec.set_names(meta, _replace_names_in_list(spec.get_names(meta), {old_name}, new_name))
                meta.updated_at = _utcnow()
                session.add(meta)
                await session.flush()
                await apply_facet_rules_to_metadata(session, meta)
                await sync_metadata_facets(session, meta)
    item = await get_facet(session, spec.kind, facet_id)
    await session.commit()
    return item


async def rename_scalar_facet(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    facet_id: int,
    new_name: str,
) -> FacetItem | None:
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return None
    if entity.name != new_name:
        conflict = (await session.exec(select(spec.entity).where(spec.entity.name == new_name))).first()
        if conflict is not None:
            raise ValueError("名称已存在, 请使用合并")
        old_name = entity.name
        await _upsert_alias(session, spec.kind, old_name, new_name)
        await _bulk_set_scalar_field(session, spec.kind, [old_name], new_name)
        entity.name = new_name
        entity.updated_at = _utcnow()
        session.add(entity)
        await session.flush()
    item = await get_facet(session, spec.kind, facet_id)
    await session.commit()
    return item


async def _merge_user_tag_facets(session: AsyncSession, target_id: int, source_id_set: set[int]) -> FacetItem | None:
    target = await session.get(UserTag, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, UserTag, source_id_set, "标签")
    target_metadata_ids = set(
        (await session.exec(select(MetadataUserTag.metadata_id).where(MetadataUserTag.user_tag_id == target_id))).all()
    )
    stmt = select(MetadataUserTag).where(col(MetadataUserTag.user_tag_id).in_(source_id_set))
    links = list((await session.exec(stmt)).all())
    for link in links:
        if link.metadata_id not in target_metadata_ids:
            session.add(MetadataUserTag(metadata_id=link.metadata_id, user_tag_id=target_id))
            target_metadata_ids.add(link.metadata_id)
        await session.delete(link)
    await session.flush()
    for src in sources:
        await session.delete(src)
    item = await get_facet(session, FacetKind.USER_TAG, target_id)
    await session.commit()
    return item


async def merge_link_facets(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    target_id: int,
    source_id_set: set[int],
) -> FacetItem | None:
    if spec.kind == FacetKind.USER_TAG:
        return await _merge_user_tag_facets(session, target_id, source_id_set)

    assert spec.get_names is not None and spec.set_names is not None
    target = await session.get(spec.entity, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, spec.entity, source_id_set, spec.label)
    old_names = {s.name for s in sources}
    if spec.kind == FacetKind.ACTOR:
        assert isinstance(target, Actor)
        actor_sources = [s for s in sources if isinstance(s, Actor)]
        await move_actor_alias_rows(session, target, actor_sources)
    elif spec.kind in SCRAPE_FACET_KINDS:
        for src_name in old_names:
            await _upsert_alias(session, spec.kind, src_name, target.name)
    stmt = select(Metadata).where(
        col(Metadata.id).in_(select(spec.link.metadata_id).where(col(spec.link_fk).in_(source_id_set)))
    )
    metadatas = list((await session.exec(stmt)).all())
    for meta in metadatas:
        spec.set_names(meta, _replace_names_in_list(spec.get_names(meta), old_names, target.name))
        meta.updated_at = _utcnow()
        session.add(meta)
        await session.flush()
        await apply_facet_rules_to_metadata(session, meta)
        await sync_metadata_facets(session, meta)
    if spec.kind == FacetKind.ACTOR:
        assert isinstance(target, Actor)
        actor_sources = [s for s in sources if isinstance(s, Actor)]
        merge_person_fields_into_target(target, actor_sources)
        target.updated_at = _utcnow()
        session.add(target)
        await session.flush()
        # 源别名行已并入 target, 显式删除 (不依赖 FK pragma).
        for src in actor_sources:
            await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == src.id))
        await session.flush()
    for src in sources:
        await session.delete(src)
    item = await get_facet(session, spec.kind, target_id)
    await session.commit()
    return item


async def merge_scalar_facets(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    target_id: int,
    source_id_set: set[int],
) -> FacetItem | None:
    target = await session.get(spec.entity, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, spec.entity, source_id_set, spec.label)
    source_names = [s.name for s in sources]
    for src_name in source_names:
        await _upsert_alias(session, spec.kind, src_name, target.name)
    await _bulk_set_scalar_field(session, spec.kind, source_names, target.name)
    for src in sources:
        await session.delete(src)
    item = await get_facet(session, spec.kind, target_id)
    await session.commit()
    return item


async def _list_link_facets(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    *,
    search: str | None,
    offset: int,
    limit: int,
    sort_by: FacetSortField,
    order: SortOrder,
) -> tuple[list[FacetItem], int]:
    entity, link, link_fk = spec.entity, spec.link, spec.link_fk

    def _match(pattern: str) -> Any:
        """展示名命中; Actor 额外命中别名行 (EXISTS, 不产生重复行)."""
        match = col(entity.name).ilike(pattern)
        if spec.alias_model is not None:
            alias = spec.alias_model
            match = or_(
                match,
                select(alias.id).where(col(alias.actor_id) == col(entity.id), col(alias.name).ilike(pattern)).exists(),
            )
        return match

    base = select(entity)
    if search:
        base = base.where(_match(f"%{search}%"))
    total = (await session.exec(select(count()).select_from(base.subquery()))).one() or 0
    count_expr = count(col(link.metadata_id))
    stmt = (
        select(col(entity.id), col(entity.name), count_expr)
        .outerjoin(link, col(link_fk) == col(entity.id))
        .group_by(col(entity.id), col(entity.name))
        .order_by(
            _facet_primary_order(sort_by, order, name_col=col(entity.name), count_expr=count_expr), col(entity.id).asc()
        )
        .offset(offset)
        .limit(limit)
    )
    if search:
        stmt = stmt.where(_match(f"%{search}%"))
    rows = (await session.exec(stmt)).all()
    return [_facet_row(r) for r in rows], int(total)


async def _list_scalar_facets(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    *,
    search: str | None,
    offset: int,
    limit: int,
    sort_by: FacetSortField,
    order: SortOrder,
) -> tuple[list[FacetItem], int]:
    entity, meta_col = spec.entity, spec.meta_col
    base = select(entity)
    if search:
        base = base.where(col(entity.name).ilike(f"%{search}%"))
    total = (await session.exec(select(count()).select_from(base.subquery()))).one() or 0
    count_expr = count(col(Metadata.id))
    stmt = (
        select(col(entity.id), col(entity.name), count_expr)
        .outerjoin(Metadata, meta_col == col(entity.name))
        .group_by(col(entity.id), col(entity.name))
        .order_by(
            _facet_primary_order(sort_by, order, name_col=col(entity.name), count_expr=count_expr), col(entity.id).asc()
        )
        .offset(offset)
        .limit(limit)
    )
    if search:
        stmt = stmt.where(col(entity.name).ilike(f"%{search}%"))
    rows = (await session.exec(stmt)).all()
    return [_facet_row(r) for r in rows], int(total)


async def list_facets(
    session: AsyncSession,
    kind: FacetKind,
    *,
    search: str | None,
    offset: int,
    limit: int,
    sort_by: FacetSortField,
    order: SortOrder,
) -> tuple[list[FacetItem], int]:
    if kind in LINK_FACETS:
        return await _list_link_facets(
            session,
            LINK_FACETS[kind],
            search=search,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            order=order,
        )
    if kind in SCALAR_FACETS:
        return await _list_scalar_facets(
            session,
            SCALAR_FACETS[kind],
            search=search,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            order=order,
        )
    raise ValueError(f"unknown facet kind: {kind}")


def _facet_row(r: tuple[object, ...]) -> FacetItem:
    facet_id, name, cnt = r[0], r[1], r[2]
    assert isinstance(facet_id, int)
    assert isinstance(name, str)
    assert cnt is None or isinstance(cnt, int)
    return FacetItem(id=facet_id, name=name, count=0 if cnt is None else cnt)


async def get_facet(session: AsyncSession, kind: FacetKind, facet_id: int) -> FacetItem | None:
    if kind in LINK_FACETS:
        spec = LINK_FACETS[kind]
        entity = await session.get(spec.entity, facet_id)
        if entity is None or entity.id is None:
            return None
        cnt = (await session.exec(select(count()).where(col(spec.link_fk) == facet_id))).one() or 0
        return FacetItem(id=entity.id, name=entity.name, count=int(cnt))
    if kind in SCALAR_FACETS:
        spec = SCALAR_FACETS[kind]
        entity = await session.get(spec.entity, facet_id)
        if entity is None or entity.id is None:
            return None
        cnt = (await session.exec(select(count()).where(spec.meta_col == entity.name))).one() or 0
        return FacetItem(id=entity.id, name=entity.name, count=int(cnt))
    return None
