"""演员浏览查询 - 人物字段 + 影片 count + 字段/范围筛选."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, and_, asc, cast, desc, func, literal, nulls_last, or_, text
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.db.models import Actor, ActorAlias, ActorSortField, MetadataActor, SortOrder
from amane.db.repo_types import ActorBrowseItem, ActorBrowseParams
from amane.enums import ActorGender


def _has_image_expr():
    """image_urls JSON 非空数组 (SQLite json_array_length)."""
    return func.json_array_length(col(Actor.image_urls)) > 0


def _has_person_expr():
    """任一人物标量/简介非空 (gender 已知亦算)."""
    return or_(
        and_(col(Actor.gender).is_not(None), col(Actor.gender) != ActorGender.UNKNOWN),
        and_(col(Actor.birthday).is_not(None), col(Actor.birthday) != ""),
        and_(col(Actor.birthplace).is_not(None), col(Actor.birthplace) != ""),
        col(Actor.height).is_not(None),
        col(Actor.bust).is_not(None),
        col(Actor.waist).is_not(None),
        col(Actor.hip).is_not(None),
        and_(col(Actor.cup).is_not(None), col(Actor.cup) != ""),
        and_(col(Actor.overview).is_not(None), col(Actor.overview) != ""),
        and_(col(Actor.tagline).is_not(None), col(Actor.tagline) != ""),
    )


def _actor_primary_order(sort_by: ActorSortField, order: SortOrder, *, count_expr):
    ascending = order == SortOrder.ASC
    if sort_by == ActorSortField.NAME:
        primary = col(Actor.name)
    elif sort_by == ActorSortField.COUNT:
        primary = count_expr
    elif sort_by == ActorSortField.UPDATED_AT:
        primary = col(Actor.updated_at)
    elif sort_by == ActorSortField.HAS_IMAGE:
        primary = func.coalesce(_has_image_expr(), literal(False))
    elif sort_by == ActorSortField.BIRTHDAY:
        primary = col(Actor.birthday)
    elif sort_by == ActorSortField.HEIGHT:
        primary = col(Actor.height)
    elif sort_by == ActorSortField.BUST:
        primary = col(Actor.bust)
    elif sort_by == ActorSortField.WAIST:
        primary = col(Actor.waist)
    elif sort_by == ActorSortField.HIP:
        primary = col(Actor.hip)
    elif sort_by == ActorSortField.CUP:
        primary = col(Actor.cup)
    else:
        primary = col(Actor.name)
    ordered = asc(primary) if ascending else desc(primary)
    # 人物指标空值沉底, 避免"无生日"在 ASC 时抢前排.
    if sort_by in (
        ActorSortField.BIRTHDAY,
        ActorSortField.HEIGHT,
        ActorSortField.BUST,
        ActorSortField.WAIST,
        ActorSortField.HIP,
        ActorSortField.CUP,
        ActorSortField.UPDATED_AT,
    ):
        return nulls_last(ordered)
    return ordered


def _append_comparable_range(
    filters: list[ColumnElement[bool]],
    column: Any,
    min_v: object | None,
    max_v: object | None,
) -> None:
    if min_v is not None:
        filters.append(column >= min_v)
    if max_v is not None:
        filters.append(column <= max_v)


def _build_browse_filters(
    params: ActorBrowseParams, *, id_subquery_sql: str | None = None
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if params.ids is not None:
        id_list = list(dict.fromkeys(params.ids))
        if not id_list:
            filters.append(col(Actor.id).is_(None))  # 空集 → 无行
        else:
            filters.append(col(Actor.id).in_(id_list))
    if id_subquery_sql is not None:
        filters.append(col(Actor.id).in_(text(id_subquery_sql)))
    if params.search:
        pattern = f"%{params.search}%"
        filters.append(
            or_(
                col(Actor.name).ilike(pattern),
                select(ActorAlias.id)
                .where(col(ActorAlias.actor_id) == col(Actor.id), col(ActorAlias.name).ilike(pattern))
                .exists(),
            )
        )
    if params.has_person is True:
        filters.append(_has_person_expr())
    elif params.has_person is False:
        filters.append(~_has_person_expr())
    if params.has_image is True:
        filters.append(_has_image_expr())
    elif params.has_image is False:
        filters.append(
            or_(
                col(Actor.image_urls).is_(None),
                func.json_array_length(col(Actor.image_urls)) == 0,
                # 兼容未初始化 / 空串
                cast(col(Actor.image_urls), String) == "[]",
            )
        )
    if params.gender:
        filters.append(col(Actor.gender).in_(list(params.gender)))
    _append_comparable_range(filters, col(Actor.birthday), params.birthday_min, params.birthday_max)
    _append_comparable_range(filters, col(Actor.height), params.height_min, params.height_max)
    _append_comparable_range(filters, col(Actor.bust), params.bust_min, params.bust_max)
    _append_comparable_range(filters, col(Actor.waist), params.waist_min, params.waist_max)
    _append_comparable_range(filters, col(Actor.hip), params.hip_min, params.hip_max)
    if params.cup_min is not None or params.cup_max is not None:
        cup_col = func.upper(col(Actor.cup))
        _append_comparable_range(filters, cup_col, params.cup_min, params.cup_max)
    if params.birthplace:
        filters.append(col(Actor.birthplace).ilike(f"%{params.birthplace}%"))
    return filters


async def browse_actors(
    session: AsyncSession, params: ActorBrowseParams, *, id_subquery_sql: str | None = None
) -> tuple[list[ActorBrowseItem], int]:
    """分页列出演员及关联影片数, 人物摘要."""
    filters = _build_browse_filters(params, id_subquery_sql=id_subquery_sql)

    base = select(Actor)
    for f in filters:
        base = base.where(f)
    total = (await session.exec(select(count()).select_from(base.subquery()))).one() or 0

    count_expr = count(col(MetadataActor.metadata_id))
    stmt = (
        select(Actor, count_expr)
        .outerjoin(MetadataActor, col(MetadataActor.actor_id) == col(Actor.id))
        .group_by(col(Actor.id))
        .order_by(_actor_primary_order(params.sort_by, params.order, count_expr=count_expr), col(Actor.id).asc())
        .offset(params.offset)
        .limit(params.limit)
    )
    for f in filters:
        stmt = stmt.where(f)

    rows = (await session.exec(stmt)).all()
    items: list[ActorBrowseItem] = []
    for row in rows:
        actor, cnt = row[0], row[1]
        assert isinstance(actor, Actor)
        assert actor.id is not None
        items.append(
            ActorBrowseItem(
                id=actor.id,
                name=actor.name,
                count=int(cnt or 0),
                gender=actor.gender if isinstance(actor.gender, ActorGender) else ActorGender(actor.gender),
                birthday=actor.birthday,
                birthplace=actor.birthplace,
                height=actor.height,
                bust=actor.bust,
                waist=actor.waist,
                hip=actor.hip,
                cup=actor.cup,
                image_urls=list(actor.image_urls or []),
                updated_at=actor.updated_at,
            )
        )
    return items, int(total)
