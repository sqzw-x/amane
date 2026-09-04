from collections.abc import Sequence
from typing import Unpack

from sqlalchemy import asc
from sqlalchemy import delete as sqla_delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ...enums import ActorGender
from ..actor_lookup import build_actor_lookup_names, list_actor_aliases, lookup_actors_by_name
from ..actor_person import actor_to_aggregated, apply_aggregated_to_actor
from ..models import (
    SCRAPE_FACET_KINDS,
    Actor,
    Comment,
    Director,
    FacetKind,
    FacetRule,
    FacetSortField,
    Metadata,
    MetadataUserTag,
    Publisher,
    Series,
    SortOrder,
    Studio,
    Tag,
    UserTag,
)
from ..repo_types import (
    ActorBrowseItem,
    ActorBrowseParams,
    ActorPersonFields,
    CommentUpdates,
    FacetItem,
    UserTagUpdates,
    _utcnow,
)
from .actor_browse import browse_actors
from .base import RepositoryMixinBase
from .facet_helpers import (
    LINK_FACETS,
    SCALAR_FACETS,
    add_one_actor_alias,
    delete_link_facet,
    delete_scalar_facet,
    get_facet,
    list_facets,
    merge_link_facets,
    merge_scalar_facets,
    normalize_names,
    remove_one_actor_alias,
    rename_link_facet,
    rename_scalar_facet,
    replace_actor_aliases,
)


async def _rows_by_names[T: Actor | Director | Tag | Studio | Publisher | Series](
    session: AsyncSession, model: type[T], names: Sequence[str]
) -> list[T]:
    """按 UNIQUE 展示名批量取回; 空名单不发 ``IN ()``."""
    unique = list(dict.fromkeys(n for n in names if n))
    if not unique:
        return []
    return list((await session.exec(select(model).where(col(model.name).in_(unique)))).all())


class FacetsRepoMixin(RepositoryMixinBase):
    async def resolve_metadata_facet_ids(
        self, meta: Metadata
    ) -> tuple[
        dict[str, int],
        dict[str, ActorGender],
        dict[str, int],
        dict[str, int],
        int | None,
        int | None,
        int | None,
    ]:
        """名称 → 分类实体 id; 演员额外带已装入行上的 gender. 未投影的名称不出现."""
        async with self._session() as session:
            actor_ids: dict[str, int] = {}
            actor_genders: dict[str, ActorGender] = {}
            for row in await _rows_by_names(session, Actor, normalize_names(meta.actors)):
                if row.id is not None:
                    actor_ids[row.name] = row.id
                    actor_genders[row.name] = row.gender
            director_ids = {
                row.name: row.id
                for row in await _rows_by_names(session, Director, normalize_names(meta.directors))
                if row.id is not None
            }
            tag_ids = {
                row.name: row.id
                for row in await _rows_by_names(session, Tag, normalize_names(meta.tags))
                if row.id is not None
            }
            studio = (await _rows_by_names(session, Studio, [meta.studio] if meta.studio else []))[:1]
            publisher = (await _rows_by_names(session, Publisher, [meta.publisher] if meta.publisher else []))[:1]
            series = (await _rows_by_names(session, Series, [meta.series] if meta.series else []))[:1]
            return (
                actor_ids,
                actor_genders,
                director_ids,
                tag_ids,
                studio[0].id if studio else None,
                publisher[0].id if publisher else None,
                series[0].id if series else None,
            )

    async def list_facets(
        self,
        kind: FacetKind,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort_by: FacetSortField = FacetSortField.NAME,
        order: SortOrder = SortOrder.ASC,
    ) -> tuple[list[FacetItem], int]:
        async with self._session() as session:
            return await list_facets(
                session, kind, search=search, offset=offset, limit=limit, sort_by=sort_by, order=order
            )

    async def get_facet(self, kind: FacetKind, facet_id: int) -> FacetItem | None:
        async with self._session() as session:
            return await get_facet(session, kind, facet_id)

    async def get_actor(self, actor_id: int) -> Actor | None:
        async with self._session() as session:
            return await session.get(Actor, actor_id)

    async def get_actor_names(self, actor_ids: Sequence[int]) -> dict[int, str]:
        """不存在的 id 不出现在结果中."""
        ids = list(dict.fromkeys(int(i) for i in actor_ids if i))
        if not ids:
            return {}
        async with self._session() as session:
            rows = (await session.exec(select(Actor.id, Actor.name).where(col(Actor.id).in_(ids)))).all()
            return {int(i): str(n) for i, n in rows if i is not None}

    async def get_actors_by_names(self, names: Sequence[str]) -> list[Actor]:
        """按展示名批量查; 不存在的名字不出现."""
        unique = list(dict.fromkeys(n for n in names if n))
        if not unique:
            return []
        async with self._session() as session:
            stmt = select(Actor).where(col(Actor.name).in_(unique))
            return list((await session.exec(stmt)).all())

    async def get_actor_lookup_names(self, actor_id: int) -> list[str] | None:
        """展示名加别名行. Actor 不存在返回 None."""
        async with self._session() as session:
            actor = await session.get(Actor, actor_id)
            if actor is None:
                return None
            return await build_actor_lookup_names(session, actor)

    async def list_actors(self, *, offset: int = 0, limit: int = 500) -> list[Actor]:
        """按 id 升序分页; 无筛选."""
        async with self._session() as session:
            stmt = select(Actor).offset(offset).limit(limit).order_by(asc(col(Actor.id)))
            return list((await session.exec(stmt)).all())

    async def browse_actors(
        self, params: ActorBrowseParams, *, id_subquery_sql: str | None = None
    ) -> tuple[list[ActorBrowseItem], int]:
        """列表行不含简介/别名/源字典."""
        async with self._session() as session:
            return await browse_actors(session, params, id_subquery_sql=id_subquery_sql)

    async def save_actor(self, actor: Actor, *, aliases: Sequence[str] | None = None) -> Actor | None:
        """不存在返回 None. ``aliases`` 提供时整表替换别名行; 省略则不动别名."""
        if actor.id is None:
            return None
        async with self._session() as session:
            db = await session.get(Actor, actor.id)
            if db is None:
                return None
            apply_aggregated_to_actor(db, actor_to_aggregated(actor))
            db.updated_at = _utcnow()
            session.add(db)
            if aliases is not None:
                await replace_actor_aliases(session, db, aliases)
            await session.commit()
            await session.refresh(db)
            return db

    async def get_actor_aliases(self, actor_id: int) -> list[str]:
        """保序; 不含展示名."""
        async with self._session() as session:
            return await list_actor_aliases(session, actor_id)

    async def lookup_actors_by_name(self, name: str) -> list[Actor]:
        """只读候选, 不创建实体."""
        async with self._session() as session:
            return await lookup_actors_by_name(session, name)

    async def add_actor_alias(self, actor_id: int, name: str) -> bool:
        """幂等追加单个别名行; 演员不存在 / 空名 / 与展示名相同 / 已存在 → False."""
        async with self._session() as session:
            actor = await session.get(Actor, actor_id)
            if actor is None:
                return False
            ok = await add_one_actor_alias(session, actor, name)
            if ok:
                await session.commit()
            return ok

    async def remove_actor_alias(self, actor_id: int, name: str) -> bool:
        """删除单个别名行; 演员不存在 / 行不存在 → False."""
        async with self._session() as session:
            if await session.get(Actor, actor_id) is None:
                return False
            ok = await remove_one_actor_alias(session, actor_id, name)
            if ok:
                await session.commit()
            return ok

    async def update_actor(self, actor_id: int, **updates: Unpack[ActorPersonFields]) -> Actor | None:
        """不修改 name/id. 不存在返回 None. ``aliases`` 经 ``replace_actor_aliases`` 整表替换."""
        async with self._session() as session:
            actor = await session.get(Actor, actor_id)
            if actor is None:
                return None
            if "aliases" in updates:
                await replace_actor_aliases(session, actor, updates["aliases"])
            if "gender" in updates:
                actor.gender = updates["gender"]
            if "birthday" in updates:
                actor.birthday = updates["birthday"]
            if "birthplace" in updates:
                actor.birthplace = updates["birthplace"]
            if "height" in updates:
                actor.height = updates["height"]
            if "bust" in updates:
                actor.bust = updates["bust"]
            if "waist" in updates:
                actor.waist = updates["waist"]
            if "hip" in updates:
                actor.hip = updates["hip"]
            if "cup" in updates:
                actor.cup = updates["cup"]
            if "overview" in updates:
                actor.overview = updates["overview"]
            if "tagline" in updates:
                actor.tagline = updates["tagline"]
            if "image_urls" in updates:
                actor.image_urls = updates["image_urls"]
            if "provider_ids" in updates:
                actor.provider_ids = updates["provider_ids"]
            if "source_urls" in updates:
                actor.source_urls = updates["source_urls"]
            if "field_sources" in updates:
                actor.field_sources = updates["field_sources"]
            if "raw" in updates:
                actor.raw = updates["raw"]
            actor.updated_at = _utcnow()
            session.add(actor)
            await session.commit()
            await session.refresh(actor)
            return actor

    async def rename_facet(self, kind: FacetKind, facet_id: int, new_name: str) -> FacetItem | None:
        """先改 Metadata 真值再重投影. 爬取侧同时写单跳 alias, 否则重刮会回到旧名.
        新名称与另一实体冲突时抛 ``ValueError``; 不存在返回 None.
        """
        async with self._session() as session:
            if kind in LINK_FACETS:
                return await rename_link_facet(session, LINK_FACETS[kind], facet_id, new_name)
            if kind in SCALAR_FACETS:
                return await rename_scalar_facet(session, SCALAR_FACETS[kind], facet_id, new_name)
            raise ValueError(f"unknown facet kind: {kind}")

    async def merge_facets(self, kind: FacetKind, target_id: int, source_ids: list[int]) -> FacetItem | None:
        """关联迁到 target, 删除 source. 爬取侧同时写压缩后的 alias.
        target 不存在返回 None; source 含 target 或不存在则抛 ``ValueError``.
        """
        source_id_set = set(source_ids)
        if target_id in source_id_set:
            raise ValueError("合并目标不能是来源之一")

        async with self._session() as session:
            if kind in LINK_FACETS:
                return await merge_link_facets(session, LINK_FACETS[kind], target_id, source_id_set)
            if kind in SCALAR_FACETS:
                return await merge_scalar_facets(session, SCALAR_FACETS[kind], target_id, source_id_set)
            raise ValueError(f"unknown facet kind: {kind}")

    async def delete_facet(self, kind: FacetKind, facet_id: int) -> bool:
        """爬取侧写 block 并从真值剔除后再删实体. user_tag 硬删挂载与实体, 不写规则. 不存在返回 False."""
        async with self._session() as session:
            if kind == FacetKind.USER_TAG:
                tag = await session.get(UserTag, facet_id)
                if tag is None:
                    return False
                await session.exec(sqla_delete(MetadataUserTag).where(col(MetadataUserTag.user_tag_id) == facet_id))
                await session.delete(tag)
                await session.commit()
                return True
            if kind in LINK_FACETS:
                return await delete_link_facet(session, LINK_FACETS[kind], facet_id)
            if kind in SCALAR_FACETS:
                return await delete_scalar_facet(session, SCALAR_FACETS[kind], facet_id)
            raise ValueError(f"unknown facet kind: {kind}")

    async def list_facet_rules(self, kind: FacetKind) -> list[FacetRule]:
        if kind not in SCRAPE_FACET_KINDS:
            raise ValueError(f"facet kind {kind} 不支持规则")
        async with self._session() as session:
            stmt = select(FacetRule).where(col(FacetRule.kind) == kind).order_by(asc(col(FacetRule.source_name)))
            return list((await session.exec(stmt)).all())

    async def delete_facet_rule(self, kind: FacetKind, rule_id: int) -> bool:
        """删除单条规则; 不回填历史 Metadata. 不存在或 kind 不匹配返回 False."""
        if kind not in SCRAPE_FACET_KINDS:
            raise ValueError(f"facet kind {kind} 不支持规则")
        async with self._session() as session:
            rule = await session.get(FacetRule, rule_id)
            if rule is None or rule.kind != kind:
                return False
            await session.delete(rule)
            await session.commit()
            return True

    async def list_user_tags(self) -> list[UserTag]:
        async with self._session() as session:
            result = await session.exec(select(UserTag).order_by(col(UserTag.name).asc()))
            return list(result.all())

    async def get_user_tag(self, user_tag_id: int) -> UserTag | None:
        async with self._session() as session:
            return await session.get(UserTag, user_tag_id)

    async def create_user_tag(self, name: str) -> UserTag:
        async with self._session() as session:
            tag = UserTag(name=name)
            session.add(tag)
            await session.commit()
            await session.refresh(tag)
            return tag

    async def update_user_tag(self, user_tag_id: int, **updates: Unpack[UserTagUpdates]) -> UserTag | None:
        async with self._session() as session:
            tag = await session.get(UserTag, user_tag_id)
            if tag is None:
                return None
            if "name" in updates:
                tag.name = updates["name"]
            tag.updated_at = _utcnow()
            session.add(tag)
            await session.commit()
            await session.refresh(tag)
            return tag

    async def delete_user_tag(self, user_tag_id: int) -> bool:
        async with self._session() as session:
            tag = await session.get(UserTag, user_tag_id)
            if tag is None:
                return False
            # 显式删除挂载, 不依赖 SQLite FK pragma
            await session.exec(sqla_delete(MetadataUserTag).where(col(MetadataUserTag.user_tag_id) == user_tag_id))
            await session.delete(tag)
            await session.commit()
            return True

    async def list_metadata_user_tags(self, metadata_id: int) -> list[UserTag]:
        async with self._session() as session:
            stmt = (
                select(UserTag)
                .join(MetadataUserTag, col(MetadataUserTag.user_tag_id) == col(UserTag.id))
                .where(col(MetadataUserTag.metadata_id) == metadata_id)
                .order_by(col(UserTag.name).asc())
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def attach_user_tag(self, metadata_id: int, user_tag_id: int) -> bool:
        """metadata/tag 不存在返回 False; 已存在则幂等成功."""
        async with self._session() as session:
            if await session.get(Metadata, metadata_id) is None:
                return False
            if await session.get(UserTag, user_tag_id) is None:
                return False
            existing = await session.get(MetadataUserTag, (metadata_id, user_tag_id))
            if existing is None:
                session.add(MetadataUserTag(metadata_id=metadata_id, user_tag_id=user_tag_id))
                await session.commit()
            return True

    async def detach_user_tag(self, metadata_id: int, user_tag_id: int) -> bool:
        async with self._session() as session:
            link = await session.get(MetadataUserTag, (metadata_id, user_tag_id))
            if link is None:
                return False
            await session.delete(link)
            await session.commit()
            return True

    async def batch_attach_user_tag(self, ids: list[int], user_tag_id: int) -> tuple[int, int]:
        """幂等. user_tag 不存在则全部计入 missing."""
        async with self._session() as session:
            if await session.get(UserTag, user_tag_id) is None:
                return 0, len(ids)
            affected = 0
            missing = 0
            for metadata_id in ids:
                if await session.get(Metadata, metadata_id) is None:
                    missing += 1
                    continue
                existing = await session.get(MetadataUserTag, (metadata_id, user_tag_id))
                if existing is None:
                    session.add(MetadataUserTag(metadata_id=metadata_id, user_tag_id=user_tag_id))
                affected += 1
            await session.commit()
            return affected, missing

    async def batch_detach_user_tag(self, ids: list[int], user_tag_id: int) -> tuple[int, int]:
        """missing 为未挂载, 或 metadata/tag 不存在的数量."""
        async with self._session() as session:
            affected = 0
            missing = 0
            for metadata_id in ids:
                link = await session.get(MetadataUserTag, (metadata_id, user_tag_id))
                if link is None:
                    missing += 1
                    continue
                await session.delete(link)
                affected += 1
            await session.commit()
            return affected, missing

    async def list_comments(self, metadata_id: int) -> list[Comment]:
        async with self._session() as session:
            stmt = (
                select(Comment)
                .where(Comment.metadata_id == metadata_id)
                .order_by(col(Comment.created_at).desc(), col(Comment.id).desc())
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def get_comment(self, comment_id: int) -> Comment | None:
        async with self._session() as session:
            return await session.get(Comment, comment_id)

    async def create_comment(self, metadata_id: int, body: str) -> Comment | None:
        async with self._session() as session:
            if await session.get(Metadata, metadata_id) is None:
                return None
            comment = Comment(metadata_id=metadata_id, body=body)
            session.add(comment)
            await session.commit()
            await session.refresh(comment)
            return comment

    async def update_comment(self, comment_id: int, **updates: Unpack[CommentUpdates]) -> Comment | None:
        async with self._session() as session:
            comment = await session.get(Comment, comment_id)
            if comment is None:
                return None
            if "body" in updates:
                comment.body = updates["body"]
            comment.updated_at = _utcnow()
            session.add(comment)
            await session.commit()
            await session.refresh(comment)
            return comment

    async def delete_comment(self, comment_id: int) -> bool:
        async with self._session() as session:
            comment = await session.get(Comment, comment_id)
            if comment is None:
                return False
            await session.delete(comment)
            await session.commit()
            return True
