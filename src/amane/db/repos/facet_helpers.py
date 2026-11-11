"""无前缀名为包内公开 API; 不经 ``amane.db.repos`` 导出面暴露."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, literal, or_, update
from sqlalchemy import delete as sqla_delete
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.functions import count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ...enums import ActorField, ActorGender
from ...parsing import split_actor_aliases
from ...utils.collections import unique_ids
from ..actor_lookup import list_actor_aliases, resolve_actor_by_name
from ..actor_person import locked_fields_of, merge_person_fields_into_target
from ..facet_rules import (
    FacetFieldValues,
    RuleEntry,
    RulesByKind,
    apply_metadata_facet_fields,
    empty_rules_by_kind,
    resolve_name,
)
from ..models import (
    FAVORITE_FACET_ENTITIES,
    SCRAPE_FACET_KINDS,
    Actor,
    ActorAlias,
    ActorUserTag,
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
from ..repo_types import (
    FacetBatchResult,
    FacetItem,
    UserTagLinkAction,
    UserTagLinkResult,
    _facet_primary_order,
    _utcnow,
)
from .base import chunks

# 关联表投影: Metadata list JSON 为真值 (actor/director/tag), 或纯挂载 (user_tag).
# 标量投影: Metadata.studio/publisher/series 字符串为真值, 实体表按 name 对齐.


type _NamedEntity = Actor | Director | Tag | UserTag | Studio | Publisher | Series

# 关联表投影对应的三个分类; 批量治理按 kind 分组重建.
LINK_KINDS: tuple[FacetKind, ...] = (FacetKind.ACTOR, FacetKind.DIRECTOR, FacetKind.TAG)


@dataclass(frozen=True, slots=True)
class _LinkFacetSpec:
    kind: FacetKind
    entity: type[Actor] | type[Director] | type[Tag] | type[UserTag]
    link: type[MetadataActor] | type[MetadataDirector] | type[MetadataTag] | type[MetadataUserTag]
    link_fk: Any
    #: 关联表的 position 列; 用户标签挂载不保序, 没有该列.
    link_position: Any = None
    label: str = ""
    get_names: Callable[[Metadata], list[str]] | None = None
    set_names: Callable[[Metadata, list[str]], None] | None = None
    #: 关联行工厂, 批量重建投影时按下标写 position.
    build: Callable[[int, int, int], MetadataActor | MetadataDirector | MetadataTag] | None = None
    alias_model: type[ActorAlias] | None = None  # 仅 Actor 搜索一并命中别名行


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
        link_position=MetadataActor.position,
        label="演员",
        get_names=_get_actors,
        set_names=_set_actors,
        build=lambda metadata_id, actor_id, position: MetadataActor(
            metadata_id=metadata_id, actor_id=actor_id, position=position
        ),
        alias_model=ActorAlias,
    ),
    FacetKind.DIRECTOR: _LinkFacetSpec(
        kind=FacetKind.DIRECTOR,
        entity=Director,
        link=MetadataDirector,
        link_fk=MetadataDirector.director_id,
        link_position=MetadataDirector.position,
        label="导演",
        get_names=_get_directors,
        set_names=_set_directors,
        build=lambda metadata_id, director_id, position: MetadataDirector(
            metadata_id=metadata_id, director_id=director_id, position=position
        ),
    ),
    FacetKind.TAG: _LinkFacetSpec(
        kind=FacetKind.TAG,
        entity=Tag,
        link=MetadataTag,
        link_fk=MetadataTag.tag_id,
        link_position=MetadataTag.position,
        label="标签",
        get_names=_get_tags,
        set_names=_set_tags,
        build=lambda metadata_id, tag_id, position: MetadataTag(
            metadata_id=metadata_id, tag_id=tag_id, position=position
        ),
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
        label="制作商",
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

# 真值列: kind → (Metadata 列名, 取值函数). 批量写回按变化列分组, 组内一条 executemany.
_FACET_COLUMNS: tuple[tuple[FacetKind, str, Callable[[FacetFieldValues], object]], ...] = (
    (FacetKind.ACTOR, "actors", lambda values: values.actors),
    (FacetKind.DIRECTOR, "directors", lambda values: values.directors),
    (FacetKind.TAG, "tags", lambda values: values.tags),
    (FacetKind.STUDIO, "studio", lambda values: values.studio),
    (FacetKind.PUBLISHER, "publisher", lambda values: values.publisher),
    (FacetKind.SERIES, "series", lambda values: values.series),
)


@dataclass(frozen=True, slots=True)
class _UserTagLinkSpec:
    """用户标签挂载的目标实体与关联表.

    影片与演员的挂载关系结构相同, 差异只在实体模型、关联模型与对应列.
    """

    entity: type[Metadata] | type[Actor]
    link: type[MetadataUserTag] | type[ActorUserTag]
    entity_col: Any
    build: Callable[[int, int], MetadataUserTag | ActorUserTag]


_METADATA_USER_TAG_LINK = _UserTagLinkSpec(
    entity=Metadata,
    link=MetadataUserTag,
    entity_col=col(MetadataUserTag.metadata_id),
    build=lambda entity_id, tag_id: MetadataUserTag(metadata_id=entity_id, user_tag_id=tag_id),
)

_ACTOR_USER_TAG_LINK = _UserTagLinkSpec(
    entity=Actor,
    link=ActorUserTag,
    entity_col=col(ActorUserTag.actor_id),
    build=lambda entity_id, tag_id: ActorUserTag(actor_id=entity_id, user_tag_id=tag_id),
)


async def purge_user_tag_links(session: AsyncSession, user_tag_id: int) -> None:
    """按标签显式删除两张关联表的挂载行, 不依赖 SQLite FK pragma."""
    await session.exec(sqla_delete(MetadataUserTag).where(col(MetadataUserTag.user_tag_id) == user_tag_id))
    await session.exec(sqla_delete(ActorUserTag).where(col(ActorUserTag.user_tag_id) == user_tag_id))


async def _apply_user_tags(
    session: AsyncSession,
    spec: _UserTagLinkSpec,
    ids: Sequence[int],
    user_tag_ids: Sequence[int],
    action: UserTagLinkAction,
) -> UserTagLinkResult:
    """把一组用户标签应用到一组条目: attach 为并入, detach 为移除, 两者均幂等.

    未知标签 id 抛 ``ValueError`` (请求级错误); 不存在的条目 id 计入 ``missing``, 其余照常处理.
    提交由调用方负责.
    """
    entity_ids = unique_ids(ids)
    tag_ids = unique_ids(user_tag_ids)
    known_tags: set[int] = set()
    if tag_ids:
        tag_rows = (await session.exec(select(UserTag.id).where(col(UserTag.id).in_(tag_ids)))).all()
        known_tags = {row for row in tag_rows if row is not None}
    unknown = [tag_id for tag_id in tag_ids if tag_id not in known_tags]
    if unknown:
        raise ValueError(f"用户标签不存在: {', '.join(str(tag_id) for tag_id in unknown)}")

    known_entities: set[int] = set()
    if entity_ids:
        entity_rows = (await session.exec(select(spec.entity.id).where(col(spec.entity.id).in_(entity_ids)))).all()
        known_entities = {row for row in entity_rows if row is not None}

    linked: set[tuple[int, int]] = set()
    if known_entities and tag_ids:
        link_rows = (
            await session.exec(
                select(col(spec.entity_col), col(spec.link.user_tag_id)).where(
                    col(spec.entity_col).in_(list(known_entities)),
                    col(spec.link.user_tag_id).in_(tag_ids),
                )
            )
        ).all()
        linked = {(entity_id, tag_id) for entity_id, tag_id in link_rows}

    if action == "detach":
        if linked:
            # 已取回的行覆盖了该筛选的全部组合, 因此按两列投影删除不会多删.
            await session.exec(
                sqla_delete(spec.link).where(
                    col(spec.entity_col).in_([entity_id for entity_id, _tag_id in linked]),
                    col(spec.link.user_tag_id).in_([tag_id for _entity_id, tag_id in linked]),
                )
            )
        changed = len({entity_id for entity_id, _tag_id in linked})
    else:
        changed = 0
        for entity_id in known_entities:
            added = False
            for tag_id in tag_ids:
                if (entity_id, tag_id) in linked:
                    continue
                session.add(spec.build(entity_id, tag_id))
                added = True
            if added:
                changed += 1

    return UserTagLinkResult(
        changed=changed,
        unchanged=len(known_entities) - changed,
        missing=len(entity_ids) - len(known_entities),
    )


async def apply_user_tags_to_metadata(
    session: AsyncSession,
    metadata_ids: Sequence[int],
    user_tag_ids: Sequence[int],
    action: UserTagLinkAction,
) -> UserTagLinkResult:
    return await _apply_user_tags(session, _METADATA_USER_TAG_LINK, metadata_ids, user_tag_ids, action)


async def apply_user_tags_to_actors(
    session: AsyncSession,
    actor_ids: Sequence[int],
    user_tag_ids: Sequence[int],
    action: UserTagLinkAction,
) -> UserTagLinkResult:
    return await _apply_user_tags(session, _ACTOR_USER_TAG_LINK, actor_ids, user_tag_ids, action)


async def _move_links_for_target_tag(
    session: AsyncSession,
    spec: _UserTagLinkSpec,
    target_tag_id: int,
    source_tag_ids: set[int],
) -> None:
    """源标签的挂载行并入 target 并按条目去重; 源行删除."""
    target_entities = set(
        (await session.exec(select(spec.entity_col).where(col(spec.link.user_tag_id) == target_tag_id))).all()
    )
    rows = (
        await session.exec(
            select(col(spec.entity_col), col(spec.link.user_tag_id)).where(
                col(spec.link.user_tag_id).in_(source_tag_ids)
            )
        )
    ).all()
    for entity_id, _tag_id in rows:
        if entity_id in target_entities:
            continue
        session.add(spec.build(entity_id, target_tag_id))
        target_entities.add(entity_id)
    if rows:
        await session.exec(sqla_delete(spec.link).where(col(spec.link.user_tag_id).in_(source_tag_ids)))
    await session.flush()


async def move_user_tag_links(
    session: AsyncSession,
    target_tag_id: int,
    source_tag_ids: set[int],
) -> None:
    """合并用户标签时迁移两张关联表的挂载行; 调用方须在删除源标签实体之前执行."""
    await _move_links_for_target_tag(session, _METADATA_USER_TAG_LINK, target_tag_id, source_tag_ids)
    await _move_links_for_target_tag(session, _ACTOR_USER_TAG_LINK, target_tag_id, source_tag_ids)


async def move_actor_user_tag_links(
    session: AsyncSession,
    target_actor_id: int,
    source_actor_ids: set[int],
) -> None:
    """合并演员时迁移标签挂载: 源演员的每个标签并入 target 并按标签去重, 源行删除.

    调用方须在删除源演员实体之前执行.
    """
    spec = _ACTOR_USER_TAG_LINK
    target_tag_ids = {
        row
        for row in (
            await session.exec(select(spec.link.user_tag_id).where(col(spec.entity_col) == target_actor_id))
        ).all()
        if row is not None
    }
    rows = (
        await session.exec(
            select(col(spec.link.user_tag_id), col(spec.entity_col)).where(col(spec.entity_col).in_(source_actor_ids))
        )
    ).all()
    for tag_id, _actor_id in rows:
        if tag_id in target_tag_ids:
            continue
        session.add(spec.build(target_actor_id, tag_id))
        target_tag_ids.add(tag_id)
    if rows:
        await session.exec(sqla_delete(spec.link).where(col(spec.entity_col).in_(source_actor_ids)))
    await session.flush()


async def _get_or_create_named[T: _NamedEntity](session: AsyncSession, model: type[T], name: str) -> T:
    existing = (await session.exec(select(model).where(model.name == name))).first()
    if existing is not None:
        return existing
    obj = model(name=name)
    session.add(obj)
    await session.flush()
    return obj


async def _named_entity_ids[T: _NamedEntity](
    session: AsyncSession, model: type[T], names: Iterable[str]
) -> dict[str, int]:
    """按名字批量取回实体 id; 不存在的名字不出现在结果里."""
    found: dict[str, int] = {}
    for chunk in chunks(sorted({name for name in names if name})):
        rows = (await session.exec(select(model.name, model.id).where(col(model.name).in_(chunk)))).all()
        found.update({str(name): int(entity_id) for name, entity_id in rows if entity_id is not None})
    return found


async def _ensure_named_entities[T: _NamedEntity](
    session: AsyncSession, model: type[T], names: Iterable[str]
) -> dict[str, int]:
    """按名字批量 get-or-create: 缺失的名字一次插入后回读, 不逐名往返."""
    wanted = {name for name in names if name}
    found = await _named_entity_ids(session, model, wanted)
    missing = sorted(wanted - found.keys())
    if not missing:
        return found
    session.add_all([model(name=name) for name in missing])
    await session.flush()
    found.update(await _named_entity_ids(session, model, missing))
    return found


async def _load_linked_facet_values(
    session: AsyncSession, spec: _LinkFacetSpec, link_ids: Sequence[int]
) -> list[tuple[int, FacetFieldValues]]:
    """受影响的影片真值六字段.

    只读治理需要的列: 逐部取 ``Metadata`` 会把 ``raw`` 等宽列一并读入, 万级关联时即成倍放大.
    同一影片可命中多个来源分块, 按影片 id 去重.
    """
    loaded: dict[int, FacetFieldValues] = {}
    for chunk in chunks(list(unique_ids(link_ids))):
        relevant = col(Metadata.id).in_(select(spec.link.metadata_id).where(col(spec.link_fk).in_(chunk)))
        list_rows = (
            await session.exec(
                select(col(Metadata.id), col(Metadata.actors), col(Metadata.directors), col(Metadata.tags)).where(
                    relevant
                )
            )
        ).all()
        scalars = (
            await session.exec(
                select(
                    col(Metadata.id),
                    col(Metadata.studio),
                    col(Metadata.publisher),
                    col(Metadata.series),
                ).where(relevant)
            )
        ).all()
        scalar_rows = {
            metadata_id: (studio, publisher, series)
            for metadata_id, studio, publisher, series in scalars
            if metadata_id is not None
        }
        for metadata_id, actors, directors, tags in list_rows:
            if metadata_id is None or metadata_id not in scalar_rows:
                continue
            studio, publisher, series = scalar_rows[metadata_id]
            loaded[metadata_id] = FacetFieldValues(
                actors=list(actors or []),
                directors=list(directors or []),
                tags=list(tags or []),
                studio=studio,
                publisher=publisher,
                series=series,
            )
    return list(loaded.items())


async def _write_facet_values(
    session: AsyncSession,
    rows: Sequence[tuple[int, FacetFieldValues, FacetFieldValues]],
    now: datetime,
) -> None:
    """按变化列分组批量写回真值; 组内一条 executemany, 不逐行 flush."""
    groups: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for metadata_id, before, after in rows:
        changed = tuple(
            (column, fetch) for kind, column, fetch in _FACET_COLUMNS if before.names(kind) != after.names(kind)
        )
        if not changed:
            continue
        groups.setdefault(tuple(column for column, _fetch in changed), []).append(
            {
                "b_metadata_id": metadata_id,
                "b_updated_at": now,
                **{f"b_{column}": fetch(after) for column, fetch in changed},
            }
        )
    connection = await session.connection()
    for columns, params in groups.items():
        statement = (
            update(Metadata)
            .where(col(Metadata.id) == bindparam("b_metadata_id"))
            .values(
                {
                    **{column: bindparam(f"b_{column}") for column in columns},
                    "updated_at": bindparam("b_updated_at"),
                }
            )
        )
        await connection.execute(statement, params)


async def _rebuild_link_projections(
    session: AsyncSession, spec: _LinkFacetSpec, rows: Sequence[tuple[int, list[str]]]
) -> None:
    """按最终真值整体重写某个 kind 的关联行: 一次删除、一次批量插入, position 取列表下标."""
    if not rows or spec.build is None:
        return
    ids_by_name = await _ensure_named_entities(
        session, spec.entity, [name for _metadata_id, names in rows for name in names]
    )
    for chunk in chunks([metadata_id for metadata_id, _names in rows]):
        await session.exec(sqla_delete(spec.link).where(col(spec.link.metadata_id).in_(chunk)))
    session.add_all(
        spec.build(metadata_id, ids_by_name[name], position)
        for metadata_id, names in rows
        for position, name in enumerate(normalize_names(names))
    )
    await session.flush()


async def _align_link_positions(
    session: AsyncSession, spec: _LinkFacetSpec, rows: Sequence[tuple[int, list[str]]]
) -> None:
    """按最终真值校正合并后的关联行: 顺序与真值不符的行整体重写, 其余保持原行.

    批量 ``UPDATE OR IGNORE`` 只保证去重, 同一影片同时挂源与目标时保留下来的 position
    不一定是真值里的位置; 只重写这部分行.
    """
    if not rows or spec.build is None or spec.link_position is None:
        return
    ids_by_name = await _ensure_named_entities(
        session, spec.entity, [name for _metadata_id, names in rows for name in names]
    )
    desired = {metadata_id: [ids_by_name[name] for name in normalize_names(names)] for metadata_id, names in rows}
    actual: dict[int, list[tuple[int, int]]] = {}
    for chunk in chunks(list(desired)):
        stmt = select(col(spec.link.metadata_id), col(spec.link_fk), col(spec.link_position)).where(
            col(spec.link.metadata_id).in_(chunk)
        )
        for metadata_id, entity_id, position in (await session.exec(stmt)).all():
            actual.setdefault(int(metadata_id), []).append((int(position), int(entity_id)))
    # position 与真值列表下标逐项对应, 不只是 id 顺序相同.
    rewrite = [
        metadata_id
        for metadata_id, entity_ids in desired.items()
        if sorted(actual.get(metadata_id, [])) != list(enumerate(entity_ids))
    ]
    if not rewrite:
        return
    for chunk in chunks(rewrite):
        await session.exec(sqla_delete(spec.link).where(col(spec.link.metadata_id).in_(chunk)))
    session.add_all(
        spec.build(metadata_id, entity_id, position)
        for metadata_id in rewrite
        for position, entity_id in enumerate(desired[metadata_id])
    )
    await session.flush()


async def _ensure_scalar_entities(
    session: AsyncSession, rows: Sequence[tuple[int, FacetFieldValues, FacetFieldValues]]
) -> None:
    """规则可能把标量分类改成还没建过的新名字; 标量投影没有关联行, 只需补实体."""
    for kind, model in ((FacetKind.STUDIO, Studio), (FacetKind.PUBLISHER, Publisher), (FacetKind.SERIES, Series)):
        names = {after.names(kind)[0] for _metadata_id, _before, after in rows if after.names(kind)}
        if names:
            await _ensure_named_entities(session, model, names)


def _is_in_place_rename(
    before: FacetFieldValues,
    after: FacetFieldValues,
    kind: FacetKind,
    old_name: str,
    new_name: str,
    kind_rules: Mapping[str, RuleEntry],
) -> bool:
    """本 kind 是否为原位改名: 只有 ``old_name`` 换成 ``new_name``, 其余字段与顺序都不变.

    还要求新名经规则解析后仍是它自己: 新名已被 alias 指向别的实体时, 真值里留下的名字
    对应另一个实体, 投影必须重建. 满足条件时关联行的实体 id 与 position 都不变
    (旧名与新名指向同一实体), 只需改写真值列.
    """
    if not before.same_except(kind, after):
        return False
    if resolve_name(new_name, kind_rules) != new_name:
        return False
    old_names = before.names(kind)
    new_names = after.names(kind)
    if len(old_names) != len(new_names) or new_names.count(new_name) != old_names.count(old_name):
        return False
    if new_name in old_names:
        # 列表里本就有目标名: 该位置原本指向另一个实体, 改名后要合并, 投影必须重建.
        return False
    return all(
        before_name == after_name or (before_name == old_name and after_name == new_name)
        for before_name, after_name in zip(old_names, new_names, strict=True)
    )


async def load_rules_by_kind(session: AsyncSession) -> dict[FacetKind, dict[str, RuleEntry]]:
    """一次读入全部治理规则; 调用方在循环外读好传给规则应用函数, 不在逐行路径上重读."""
    out = empty_rules_by_kind()
    rows = list((await session.exec(select(FacetRule))).all())
    for row in rows:
        kind = row.kind if isinstance(row.kind, FacetKind) else FacetKind(row.kind)
        if kind not in SCRAPE_FACET_KINDS:
            continue
        action = row.action if isinstance(row.action, FacetRuleAction) else FacetRuleAction(row.action)
        out[kind][row.source_name] = RuleEntry(action=action, target_name=row.target_name)
    return out


async def apply_facet_rules_to_metadata(session: AsyncSession, meta: Metadata, rules_by_kind: RulesByKind) -> None:
    """就地执行治理规则并落在当前事务; 规则表由调用方一次读好后复用."""
    apply_metadata_facet_fields(meta, rules_by_kind)
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
    """规则行唯一写入点. actor 不允许 alias 规则, 由 ActorAlias 承担."""
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
    """写入单跳 alias 并压缩入边; 目标已被 block 时改为 block source. 禁止 actor kind."""
    if kind == FacetKind.ACTOR:
        raise ValueError("演员别名规则已由 actor_aliases 表取代")
    if kind not in SCRAPE_FACET_KINDS:
        raise ValueError(f"facet kind {kind} 不支持别名规则")
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
        raise ValueError(f"facet kind {kind} 不支持剔除规则")
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
    """从真值剔除名字并删除对应关联行; 不逐部影片重建投影.

    剔除只会让名字从列表里消失, 剩余名字的相对顺序不变: 关联行按实体整体删除,
    真值按变化列批量写回, 代价与命中的影片数无关.
    """
    if not names or spec.get_names is None or spec.build is None:
        return
    entity_ids = list((await _named_entity_ids(session, spec.entity, names)).values())
    if not entity_ids:
        return
    loaded = await _load_linked_facet_values(session, spec, entity_ids)
    rewrites = [
        (metadata_id, before, before.with_names(spec.kind, [n for n in before.names(spec.kind) if n not in names]))
        for metadata_id, before in loaded
    ]
    await _write_facet_values(session, rewrites, _utcnow())
    await session.exec(sqla_delete(spec.link).where(col(spec.link_fk).in_(entity_ids)))
    await session.flush()
    # 关联行已删, 剩下的 position 出现空位; 按最终真值下标补齐, 与其它写入路径保持同一形态.
    await _align_link_positions(
        session, spec, [(metadata_id, after.names(spec.kind)) for metadata_id, _before, after in rewrites]
    )


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
    """写剔除规则, 从真值移除名字后删除实体; 提交由调用方负责."""
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
        # 显式删除别名行与用户标签挂载, 不依赖 FK pragma.
        await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == facet_id))
        await session.exec(sqla_delete(ActorUserTag).where(col(ActorUserTag.actor_id) == facet_id))
    await session.delete(entity)
    return True


async def delete_scalar_facet(session: AsyncSession, spec: _ScalarFacetSpec, facet_id: int) -> bool:
    """写剔除规则并清空真值里的该标量; 提交由调用方负责."""
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return False
    blocked = await _upsert_block(session, spec.kind, entity.name)
    await _strip_names_from_scalar_metadata(session, spec.kind, blocked)
    await session.delete(entity)
    return True


def normalize_names(names: list[str] | None) -> list[str]:
    if not names:
        return []
    return [n for n in names if isinstance(n, str) and n]


async def resolve_scalar_facet_names(
    session: AsyncSession, model: type[Studio] | type[Publisher] | type[Series], ids: Sequence[int] | None
) -> list[str]:
    """未知 id 跳过."""
    names: list[str] = []
    for facet_id in unique_ids(ids):
        entity = await session.get(model, facet_id)
        if entity is not None:
            names.append(entity.name)
    return names


def _replace_names_in_list(values: list[str], old_names: set[str], new_name: str) -> list[str]:
    """命中 old_names 的元素替换为 new_name, 保序去重."""
    result: list[str] = []
    for v in values:
        replaced = new_name if v in old_names else v
        if replaced not in result:
            result.append(replaced)
    return result


async def cascade_delete_metadata(session: AsyncSession, metadata: Metadata) -> None:
    """不提交事务. 必须 nullify MediaFile.metadata_id 并回 PENDING, 否则留下悬空 FK;
    文件记录本身保留. 分类关联 / 评论 / 用户 tag 须显式删除 (不依赖 FK pragma);
    Actor/Director 等目录实体本身保留.
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


# 展示名不入表. 空名或与展示名相同的项丢弃; 幂等可重复调用.


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
    """去重去空并跳过展示名; 无新行时不做任何写."""
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
    """整表替换别名行 (保序写入 position); 不动 person 字段."""
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
    """名字为空或行不存在 → False. 展示名本身不在行内."""
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
    """新名行出表, 旧展示名入表 (追加末尾). 不修改 ``Actor.name``; 须在展示名尚未修改时调用."""
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
    """源展示名与别名行并入 target. 已有行在前; target 展示名不入表."""
    names: list[str] = list(await list_actor_aliases(session, target.id or 0))
    for src in sources:
        names.append(src.name)
        names.extend(await list_actor_aliases(session, src.id or 0))
    await replace_actor_aliases(session, target, names)


def _is_blocked(rules: Mapping[str, RuleEntry], name: str) -> bool:
    """仅认 block 行."""
    rule = rules.get(name)
    return rule is not None and rule.action == FacetRuleAction.BLOCK


async def clean_actor_names(
    session: AsyncSession,
    meta: Metadata,
    actor_genders: Mapping[str, ActorGender] | None = None,
    rules_by_kind: RulesByKind | None = None,
) -> None:
    """拆 ``name(alias1, alias2)``: 展示名留真值, 别名并入 ActorAlias.
    须在 ``apply_facet_rules_to_metadata`` 之前运行. block 在解析前查原始名、解析后查展示名.
    名单不变时不改 ``Metadata.actors``. ``Actor.gender`` 为 ``unknown`` 且入参给出
    ``female`` / ``male`` 时填空; 不覆盖已有性别, 不写入 ``field_sources``; 性别已锁则跳过.
    别名并入同样跳过别名已锁的演员. ``rules_by_kind`` 省略时现读规则表 (调用方已读好则复用).
    """
    raw_names = normalize_names(meta.actors)
    if not raw_names:
        return
    genders = actor_genders or {}
    rules = rules_by_kind if rules_by_kind is not None else await load_rules_by_kind(session)
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
        seed = genders.get(raw) or genders.get(name)
        if (
            seed is not None
            and seed != ActorGender.UNKNOWN
            and actor.gender == ActorGender.UNKNOWN
            and ActorField.GENDER not in locked_fields_of(actor)
        ):
            actor.gender = seed
            session.add(actor)
        if actor.name not in seen:
            seen.add(actor.name)
            canonical.append(actor.name)
        if aliases:
            assert actor.id is not None
            alias_targets.setdefault(actor.id, []).extend(aliases)
    for actor_id, aliases in alias_targets.items():
        actor = await session.get(Actor, actor_id)
        if actor is not None and ActorField.ALIASES not in locked_fields_of(actor):
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
    """按 id 批量取回来源实体; 缺失时抛 ``ValueError``."""
    found: dict[int, T] = {}
    ordered = sorted(source_ids)
    for chunk in chunks(ordered):
        rows = (await session.exec(select(model).where(col(model.id).in_(chunk)))).all()
        found.update({int(row.id): row for row in rows if row.id is not None})
    missing = [sid for sid in ordered if sid not in found]
    if missing:
        raise ValueError(f"来源{label} {missing[0]} 不存在")
    return [found[sid] for sid in ordered]


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


async def _rewrite_renamed_metadata(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    loaded: Sequence[tuple[int, FacetFieldValues]],
    old_name: str,
    new_name: str,
    rules_by_kind: RulesByKind,
) -> None:
    """改写真值, 按最终实体与顺序重建投影.

    实体已经改名, 即使规则执行后的真值未变, 关联行也可能指向错误的名字;
    本分类仅在原位改名仍满足最终真值时保留投影, 其它分类按真值变化重建.
    """
    rewrites: list[tuple[int, FacetFieldValues, FacetFieldValues]] = []
    rebuild: dict[FacetKind, list[tuple[int, list[str]]]] = {}
    for metadata_id, before in loaded:
        after = before.with_names(
            spec.kind, _replace_names_in_list(before.names(spec.kind), {old_name}, new_name)
        ).apply_rules(rules_by_kind)
        if after != before:
            rewrites.append((metadata_id, before, after))
        in_place = _is_in_place_rename(before, after, spec.kind, old_name, new_name, rules_by_kind.get(spec.kind, {}))
        for kind in LINK_KINDS:
            needs_rebuild = not in_place if kind == spec.kind else before.names(kind) != after.names(kind)
            if needs_rebuild:
                rebuild.setdefault(kind, []).append((metadata_id, after.names(kind)))
    await _write_facet_values(session, rewrites, _utcnow())
    for kind, rows in rebuild.items():
        await _rebuild_link_projections(session, LINK_FACETS[kind], rows)
    await _ensure_scalar_entities(session, rewrites)


async def _move_links_to_target(
    session: AsyncSession, spec: _LinkFacetSpec, target_id: int, source_ids: set[int]
) -> None:
    """批量把源关联行改成 target; 该影片已有 target 行时保留既有行, 其余删掉.

    ``UPDATE OR IGNORE`` 让重复的 (影片, target) 不违反主键; 剩下仍是源 id 的行即冲突行.
    """
    for chunk in chunks(sorted(source_ids)):
        await session.exec(
            update(spec.link)
            .where(col(spec.link_fk).in_(chunk))
            .values({spec.link_fk: target_id})
            .prefix_with("OR IGNORE")
        )
        await session.exec(sqla_delete(spec.link).where(col(spec.link_fk).in_(chunk)))
    await session.flush()


async def rename_link_facet(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    facet_id: int,
    new_name: str,
) -> FacetItem | None:
    """实体原位改名: 真值里的旧名换成新名.

    本分类的投影只在原位改名时保留 (只有旧名换成新名, 实体与 position 都不变); 新名经规则
    解析后指向别的实体、真值因此增删或换序时, 即使真值文本未变也要按最终真值重建.
    调用方须先取得写锁并负责提交.
    """
    entity = await session.get(spec.entity, facet_id)
    if entity is None:
        return None
    if entity.name != new_name:
        conflict = (await session.exec(select(spec.entity).where(spec.entity.name == new_name))).first()
        if conflict is not None:
            raise ValueError("名称已存在, 请使用合并")
        old_name = entity.name
        # 治理规则先落库, 规则表随后一次读入: 新名字可能命中既有 alias / block.
        if spec.kind == FacetKind.ACTOR:
            assert isinstance(entity, Actor)
            await swap_actor_display_name(session, entity, old_name, new_name)
        elif spec.kind in SCRAPE_FACET_KINDS:
            await _upsert_alias(session, spec.kind, old_name, new_name)
        entity.name = new_name
        entity.updated_at = _utcnow()
        session.add(entity)
        await session.flush()
        if spec.get_names is not None:
            rules_by_kind = await load_rules_by_kind(session)
            loaded = await _load_linked_facet_values(session, spec, [facet_id])
            await _rewrite_renamed_metadata(session, spec, loaded, old_name, new_name, rules_by_kind)
    return await get_facet(session, spec.kind, facet_id)


async def rename_scalar_facet(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    facet_id: int,
    new_name: str,
) -> FacetItem | None:
    """标量分类改名: 写别名规则后批量改写真值列. 调用方须先取得写锁并负责提交."""
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
    return await get_facet(session, spec.kind, facet_id)


async def _merge_user_tag_facets(session: AsyncSession, target_id: int, source_id_set: set[int]) -> FacetItem | None:
    target = await session.get(UserTag, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, UserTag, source_id_set, "标签")
    await move_user_tag_links(session, target_id, source_id_set)
    for src in sources:
        await session.delete(src)
    return await get_facet(session, FacetKind.USER_TAG, target_id)


async def merge_link_facets(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    target_id: int,
    source_id_set: set[int],
) -> FacetItem | None:
    """关联迁到 target 并删除 source.

    真值里的源名替换为 target 名后仍按规则收敛; 关联行用批量 ``UPDATE OR IGNORE`` 去重,
    再按最终真值的顺序校正 position. 调用方须先取得写锁并负责提交.
    """
    if spec.kind == FacetKind.USER_TAG:
        return await _merge_user_tag_facets(session, target_id, source_id_set)

    assert spec.get_names is not None
    target = await session.get(spec.entity, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, spec.entity, source_id_set, spec.label)
    source_names = {source.name for source in sources}
    actor_sources = [source for source in sources if isinstance(source, Actor)]
    if spec.kind == FacetKind.ACTOR:
        assert isinstance(target, Actor)
        await move_actor_alias_rows(session, target, actor_sources)
    elif spec.kind in SCRAPE_FACET_KINDS:
        for source_name in sorted(source_names):
            await _upsert_alias(session, spec.kind, source_name, target.name)
    rules_by_kind = await load_rules_by_kind(session)
    loaded = await _load_linked_facet_values(session, spec, sorted(source_id_set))
    rewrites: list[tuple[int, FacetFieldValues, FacetFieldValues]] = []
    final_names: list[tuple[int, list[str]]] = []
    rebuild: dict[FacetKind, list[tuple[int, list[str]]]] = {}
    for metadata_id, before in loaded:
        after = before.with_names(
            spec.kind, _replace_names_in_list(before.names(spec.kind), source_names, target.name)
        ).apply_rules(rules_by_kind)
        final_names.append((metadata_id, after.names(spec.kind)))
        if after != before:
            rewrites.append((metadata_id, before, after))
        for kind in LINK_KINDS:
            if kind == spec.kind:
                continue  # 本 kind 由批量 UPDATE OR IGNORE 去重后再校正 position
            if before.names(kind) != after.names(kind):
                rebuild.setdefault(kind, []).append((metadata_id, after.names(kind)))
    await _write_facet_values(session, rewrites, _utcnow())
    await _move_links_to_target(session, spec, target_id, source_id_set)
    await _align_link_positions(session, spec, final_names)
    for kind, rows in rebuild.items():
        await _rebuild_link_projections(session, LINK_FACETS[kind], rows)
    await _ensure_scalar_entities(session, rewrites)
    if spec.kind == FacetKind.ACTOR:
        assert isinstance(target, Actor)
        merge_person_fields_into_target(target, actor_sources)
        target.updated_at = _utcnow()
        session.add(target)
        await session.flush()
        # 源标签挂载与别名行已并入 target, 须显式删除 (不依赖 FK pragma).
        await move_actor_user_tag_links(session, target_id, {s.id for s in actor_sources if s.id is not None})
        for src in actor_sources:
            await session.exec(sqla_delete(ActorAlias).where(col(ActorAlias.actor_id) == src.id))
        await session.flush()
    await _carry_favorites_into_target(session, spec.kind, target_id, source_id_set)
    for source in sources:
        await session.delete(source)
    return await get_facet(session, spec.kind, target_id)


async def merge_scalar_facets(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    target_id: int,
    source_id_set: set[int],
) -> FacetItem | None:
    """标量分类合并: 源名写成指向 target 的别名规则, 真值列一次批量改写."""
    target = await session.get(spec.entity, target_id)
    if target is None:
        return None
    sources = await _load_named_sources(session, spec.entity, source_id_set, spec.label)
    source_names = [s.name for s in sources]
    for src_name in source_names:
        await _upsert_alias(session, spec.kind, src_name, target.name)
    await _bulk_set_scalar_field(session, spec.kind, source_names, target.name)
    await _carry_favorites_into_target(session, spec.kind, target_id, source_id_set)
    for src in sources:
        await session.delete(src)
    return await get_facet(session, spec.kind, target_id)


def _favorite_entity(
    kind: FacetKind,
) -> type[Director] | type[Tag] | type[Studio] | type[Publisher] | type[Series] | None:
    """可收藏的实体模型; 不支持收藏的分类返回 None.

    收藏列与实体同源 (``FAVORITE_FACET_ENTITIES``): 列表 / 详情 / 写入 / 合并都经这里取,
    不在各分支另写 kind 名单.
    """
    return FAVORITE_FACET_ENTITIES.get(kind)


def _favorite_column(kind: FacetKind) -> Any | None:
    """收藏列表达式; 不支持收藏的分类返回 None."""
    entity = _favorite_entity(kind)
    return None if entity is None else col(entity.is_favorite)


def _favorite_condition(kind: FacetKind, favorite: bool) -> Any:
    column = _favorite_column(kind)
    if column is None:
        raise ValueError(f"facet kind {kind} 不支持收藏")
    return column.is_(favorite)


async def _facet_favorite(session: AsyncSession, kind: FacetKind, facet_id: int) -> bool:
    entity = _favorite_entity(kind)
    if entity is None:
        return False
    value = (await session.exec(select(col(entity.is_favorite)).where(col(entity.id) == facet_id))).first()
    return bool(value)


async def _carry_favorites_into_target(
    session: AsyncSession, kind: FacetKind, target_id: int, source_id_set: set[int]
) -> None:
    """合并前把源的收藏并入目标.

    源实体随后被删除, 收藏位随行消失等于在用户的显式操作下静默丢失; 目标自身已收藏时保持真值,
    合并不取消目标收藏.
    """
    entity = _favorite_entity(kind)
    if entity is None or not source_id_set:
        return
    hit = (
        await session.exec(select(count()).where(col(entity.id).in_(source_id_set), col(entity.is_favorite).is_(True)))
    ).one()
    if not hit:
        return
    target = await session.get(entity, target_id)
    if target is None:
        return
    target.is_favorite = True
    await session.flush()


async def _list_link_facets(
    session: AsyncSession,
    spec: _LinkFacetSpec,
    *,
    search: str | None,
    favorite: bool | None,
    offset: int,
    limit: int,
    sort_by: FacetSortField,
    order: SortOrder,
) -> tuple[list[FacetItem], int]:
    entity, link, link_fk = spec.entity, spec.link, spec.link_fk
    favorite_column = _favorite_column(spec.kind)
    favorite_expr: Any = literal(False) if favorite_column is None else favorite_column
    favorite_condition = None if favorite is None else _favorite_condition(spec.kind, favorite)

    def _match(pattern: str) -> Any:
        """展示名命中; Actor 额外以 EXISTS 命中别名行, 不产生重复行."""
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
    if favorite_condition is not None:
        base = base.where(favorite_condition)
    total = (await session.exec(select(count()).select_from(base.subquery()))).one() or 0
    count_expr = count(col(link.metadata_id))
    group_by: list[Any] = [col(entity.id), col(entity.name)]
    if favorite_column is not None:
        # group_by 与 select 必须同步扩列; 常量占位不进 group_by.
        group_by.append(favorite_column)
    stmt = (
        select(col(entity.id), col(entity.name), count_expr, favorite_expr)
        .outerjoin(link, col(link_fk) == col(entity.id))
        .group_by(*group_by)
        .order_by(
            _facet_primary_order(sort_by, order, name_col=col(entity.name), count_expr=count_expr), col(entity.id).asc()
        )
        .offset(offset)
        .limit(limit)
    )
    if search:
        stmt = stmt.where(_match(f"%{search}%"))
    if favorite_condition is not None:
        stmt = stmt.where(favorite_condition)
    rows = (await session.exec(stmt)).all()
    return [_facet_row(r) for r in rows], int(total)


async def _list_scalar_facets(
    session: AsyncSession,
    spec: _ScalarFacetSpec,
    *,
    search: str | None,
    favorite: bool | None,
    offset: int,
    limit: int,
    sort_by: FacetSortField,
    order: SortOrder,
) -> tuple[list[FacetItem], int]:
    entity, meta_col = spec.entity, spec.meta_col
    favorite_column = _favorite_column(spec.kind)
    favorite_expr: Any = literal(False) if favorite_column is None else favorite_column
    favorite_condition = None if favorite is None else _favorite_condition(spec.kind, favorite)
    base = select(entity)
    if search:
        base = base.where(col(entity.name).ilike(f"%{search}%"))
    if favorite_condition is not None:
        base = base.where(favorite_condition)
    total = (await session.exec(select(count()).select_from(base.subquery()))).one() or 0
    count_expr = count(col(Metadata.id))
    group_by: list[Any] = [col(entity.id), col(entity.name)]
    if favorite_column is not None:
        # group_by 与 select 必须同步扩列; 常量占位不进 group_by.
        group_by.append(favorite_column)
    stmt = (
        select(col(entity.id), col(entity.name), count_expr, favorite_expr)
        .outerjoin(Metadata, meta_col == col(entity.name))
        .group_by(*group_by)
        .order_by(
            _facet_primary_order(sort_by, order, name_col=col(entity.name), count_expr=count_expr), col(entity.id).asc()
        )
        .offset(offset)
        .limit(limit)
    )
    if search:
        stmt = stmt.where(col(entity.name).ilike(f"%{search}%"))
    if favorite_condition is not None:
        stmt = stmt.where(favorite_condition)
    rows = (await session.exec(stmt)).all()
    return [_facet_row(r) for r in rows], int(total)


async def list_facets(
    session: AsyncSession,
    kind: FacetKind,
    *,
    search: str | None,
    favorite: bool | None = None,
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
            favorite=favorite,
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
            favorite=favorite,
            offset=offset,
            limit=limit,
            sort_by=sort_by,
            order=order,
        )
    raise ValueError(f"unknown facet kind: {kind}")


def _facet_row(r: tuple[object, ...]) -> FacetItem:
    facet_id, name, cnt, favorite = r[0], r[1], r[2], r[3]
    assert isinstance(facet_id, int)
    assert isinstance(name, str)
    assert cnt is None or isinstance(cnt, int)
    assert isinstance(favorite, bool | int)
    return FacetItem(id=facet_id, name=name, count=0 if cnt is None else cnt, is_favorite=bool(favorite))


async def get_facet(session: AsyncSession, kind: FacetKind, facet_id: int) -> FacetItem | None:
    if kind in LINK_FACETS:
        spec = LINK_FACETS[kind]
        entity = await session.get(spec.entity, facet_id)
        if entity is None or entity.id is None:
            return None
        cnt = (await session.exec(select(count()).where(col(spec.link_fk) == facet_id))).one() or 0
        # tag 是 link 分类, 收藏列同样取自实体行.
        favorite = await _facet_favorite(session, kind, facet_id)
        return FacetItem(id=entity.id, name=entity.name, count=int(cnt), is_favorite=favorite)
    if kind in SCALAR_FACETS:
        spec = SCALAR_FACETS[kind]
        entity = await session.get(spec.entity, facet_id)
        if entity is None or entity.id is None:
            return None
        cnt = (await session.exec(select(count()).where(spec.meta_col == entity.name))).one() or 0
        favorite = await _facet_favorite(session, kind, facet_id)
        return FacetItem(id=entity.id, name=entity.name, count=int(cnt), is_favorite=favorite)
    return None


async def set_facets_favorite(
    session: AsyncSession, kind: FacetKind, facet_ids: Sequence[int], is_favorite: bool
) -> FacetBatchResult:
    """把一批分类的收藏位整体赋同一个取值, 单个事务; 分类不支持收藏抛 ``ValueError``.

    只写取值与目标不同的行, 因此重复提交同一批不产生写入. 不存在的 id 计入 ``missing``, 其余
    照常处理 — 批量请求里某个 id 已失效不该让整批无结果. 提交由调用方负责.
    """
    entity = _favorite_entity(kind)
    if entity is None:
        raise ValueError(f"facet kind {kind} 不支持收藏")
    ids = unique_ids(facet_ids)
    rows = (await session.exec(select(entity).where(col(entity.id).in_(ids)))).all()
    changed = 0
    for row in rows:
        if row.is_favorite == is_favorite:
            continue
        row.is_favorite = is_favorite
        row.updated_at = _utcnow()
        session.add(row)
        changed += 1
    return FacetBatchResult(
        changed=changed,
        unchanged=len(rows) - changed,
        missing=len(ids) - len(rows),
    )
