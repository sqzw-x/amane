from collections.abc import Sequence
from typing import Unpack

from sqlalchemy import asc
from sqlalchemy import delete as sqla_delete
from sqlmodel import col, select

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


class FacetsRepoMixin(RepositoryMixinBase):
    async def resolve_metadata_facet_ids(
        self, meta: Metadata
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int], int | None, int | None, int | None]:
        """将 metadata 上的名称解析为分类实体 id (供详情页 Badge 跳转)."""
        async with self._session() as session:
            actor_ids: dict[str, int] = {}
            for name in normalize_names(meta.actors):
                row = (await session.exec(select(Actor).where(Actor.name == name))).first()
                if row is not None and row.id is not None:
                    actor_ids[name] = row.id
            director_ids: dict[str, int] = {}
            for name in normalize_names(meta.directors):
                row = (await session.exec(select(Director).where(Director.name == name))).first()
                if row is not None and row.id is not None:
                    director_ids[name] = row.id
            tag_ids: dict[str, int] = {}
            for name in normalize_names(meta.tags):
                row = (await session.exec(select(Tag).where(Tag.name == name))).first()
                if row is not None and row.id is not None:
                    tag_ids[name] = row.id
            studio_id: int | None = None
            if meta.studio:
                row = (await session.exec(select(Studio).where(Studio.name == meta.studio))).first()
                if row is not None:
                    studio_id = row.id
            publisher_id: int | None = None
            if meta.publisher:
                row = (await session.exec(select(Publisher).where(Publisher.name == meta.publisher))).first()
                if row is not None:
                    publisher_id = row.id
            series_id: int | None = None
            if meta.series:
                row = (await session.exec(select(Series).where(Series.name == meta.series))).first()
                if row is not None:
                    series_id = row.id
            return actor_ids, director_ids, tag_ids, studio_id, publisher_id, series_id

    async def list_facets(
        self,
        kind: FacetKind,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort_by: FacetSortField = FacetSortField.NAME,
        order: SortOrder = SortOrder.ASC,
    ) -> tuple[list[FacetItem], int]:
        """分页列出分类目录条目及关联影片数."""
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
        """批量取 Actor 名 (任务列表标题投影用); 不存在的 id 不出现在结果中."""
        ids = list(dict.fromkeys(int(i) for i in actor_ids if i))
        if not ids:
            return {}
        async with self._session() as session:
            rows = (await session.exec(select(Actor.id, Actor.name).where(col(Actor.id).in_(ids)))).all()
            return {int(i): str(n) for i, n in rows if i is not None}

    async def get_actors_by_names(self, names: Sequence[str]) -> list[Actor]:
        """按规范名批量查 Actor 行 (链式刮削跳过判断用); 不存在的名字不出现在结果中."""
        unique = list(dict.fromkeys(n for n in names if n))
        if not unique:
            return []
        async with self._session() as session:
            stmt = select(Actor).where(col(Actor.name).in_(unique))
            return list((await session.exec(stmt)).all())

    async def get_actor_lookup_names(self, actor_id: int) -> list[str] | None:
        """刮削查找名; Actor 不存在返回 None."""
        async with self._session() as session:
            actor = await session.get(Actor, actor_id)
            if actor is None:
                return None
            return await build_actor_lookup_names(session, actor)

    async def list_actors(self, *, offset: int = 0, limit: int = 500) -> list[Actor]:
        """分页列出 Actor 行 (cleanup 收集 image_urls 等)."""
        async with self._session() as session:
            stmt = select(Actor).offset(offset).limit(limit).order_by(asc(col(Actor.id)))
            return list((await session.exec(stmt)).all())

    async def browse_actors(
        self, params: ActorBrowseParams, *, id_subquery_sql: str | None = None
    ) -> tuple[list[ActorBrowseItem], int]:
        """演员浏览分页列表 (人物摘要 + 影片 count)."""
        async with self._session() as session:
            return await browse_actors(session, params, id_subquery_sql=id_subquery_sql)

    async def save_actor(self, actor: Actor, *, aliases: Sequence[str] | None = None) -> Actor | None:
        """将已修改的 Actor 人物字段持久化; 不存在返回 None.

        ``aliases`` 提供时整表替换该演员别名行 (刮削回写 / 测试用); 省略不动别名行.
        """
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
        """演员别名行 (保序); 不含展示名."""
        async with self._session() as session:
            return await list_actor_aliases(session, actor_id)

    async def lookup_actors_by_name(self, name: str) -> list[Actor]:
        """只读名字→演员候选 (展示名命中 + 别名行命中); 不创建实体."""
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
        """按字段更新 Actor 人物元数据; 不改 name/id. 不存在返回 None.

        ``aliases`` 走 ``replace_actor_aliases`` 整表替换 (保序).
        """
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
        """重命名分类实体 (含用户 tag).

        Metadata 的 JSON/标量字段是刮削真值, 分类实体表是查询投影 -- 重命名先改 JSON/标量,
        再走 ``sync_metadata_facets`` 重投影. 爬取侧同时写入单跳 alias 规则, 使重刮仍映射到新名.
        新名称与另一实体冲突时抛 ``ValueError`` (语义上是"改名成了合并", 由调用方决定是否引导
        用户走 :meth:`merge_facets`). 不存在的 facet_id 返回 None.
        """
        async with self._session() as session:
            if kind in LINK_FACETS:
                return await rename_link_facet(session, LINK_FACETS[kind], facet_id, new_name)
            if kind in SCALAR_FACETS:
                return await rename_scalar_facet(session, SCALAR_FACETS[kind], facet_id, new_name)
            raise ValueError(f"unknown facet kind: {kind}")

    async def merge_facets(self, kind: FacetKind, target_id: int, source_ids: list[int]) -> FacetItem | None:
        """将 source_ids 合并入 target_id: 关联迁移到 target, 删除 source 实体.

        爬取侧同时写入压缩后的 alias 规则. target 不存在返回 None; source_ids 含 target_id
        或引用不存在的实体抛 ``ValueError``.
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
        """删除分类实体.

        爬取侧: 写 block 规则 (入边 alias 压成 block), 从 Metadata 真值剔除, 再删实体.
        user_tag: 硬删挂载与实体, 不写规则. 不存在返回 False.
        """
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
            # 显式清挂载, 不依赖 SQLite FK pragma
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
        """挂载用户 tag. 返回 False 若 metadata/tag 不存在; 已存在则幂等成功."""
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
        """批量挂载用户 tag (幂等). user_tag 不存在则全部计入 missing.

        返回 (affected, missing) -- missing 为不存在的 metadata id 数 (或 tag 不存在时的全部 id 数).
        """
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
        """批量取消挂载用户 tag. 返回 (affected, missing) -- missing 为未挂载 (或 metadata/tag 不存在) 的数量."""
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
