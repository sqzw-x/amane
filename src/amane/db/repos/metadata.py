from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Unpack

from sqlalchemy import func, or_, text
from sqlalchemy.sql.functions import count
from sqlmodel import col, select

from ...enums import ActorGender
from ...parsing import ContentType, Mosaic
from ..models import (
    MediaFile,
    Metadata,
    MetadataActor,
    MetadataDirector,
    MetadataSortField,
    MetadataTag,
    MetadataUserTag,
    Publisher,
    Series,
    SortOrder,
    Studio,
)
from ..repo_types import (
    MetadataFields,
    _media_file_uncensored_predicate,
    _metadata_has_files_clause,
    _metadata_linked_file_exists,
    _metadata_primary_order,
    _utcnow,
)
from .base import RepositoryMixinBase
from .facet_helpers import (
    apply_facet_rules_to_metadata,
    cascade_delete_metadata,
    clean_actor_names,
    resolve_scalar_facet_names,
    sync_metadata_facets,
    unique_ids,
)


class MetadataRepoMixin(RepositoryMixinBase):
    async def list_metadata(
        self,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: MetadataSortField = MetadataSortField.UPDATED_AT,
        order: SortOrder = SortOrder.DESC,
        actor_ids: Sequence[int] | None = None,
        director_ids: Sequence[int] | None = None,
        tag_ids: Sequence[int] | None = None,
        studio_ids: Sequence[int] | None = None,
        publisher_ids: Sequence[int] | None = None,
        series_ids: Sequence[int] | None = None,
        user_tag_ids: Sequence[int] | None = None,
        has_files: bool | None = None,
        has_subtitle: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
        ids: Sequence[int] | None = None,
        id_subquery_sql: str | None = None,
        updated_before: datetime | None = None,
    ) -> tuple[list[Metadata], int]:
        """关联类 facet 同 kind 多 id 为 AND; 标量类同 kind 多 id 为 OR (未知 id 忽略, 全部未知则空).
        跨 kind 始终 AND. 相位筛选项: 布尔 True=至少一份具备, False=没有任何一份具备.
        """
        async with self._session() as session:
            base = select(Metadata)
            if ids is not None:
                id_list = list(unique_ids(ids))
                if not id_list:
                    return [], 0
                base = base.where(col(Metadata.id).in_(id_list))
            if id_subquery_sql is not None:
                base = base.where(col(Metadata.id).in_(text(id_subquery_sql)))
            if keyword:
                search_pattern = f"%{keyword}%"
                base = base.where(
                    or_(col(Metadata.number).ilike(search_pattern), col(Metadata.title).ilike(search_pattern))
                )
            for actor_id in unique_ids(actor_ids):
                base = base.where(
                    col(Metadata.id).in_(select(MetadataActor.metadata_id).where(MetadataActor.actor_id == actor_id))
                )
            for director_id in unique_ids(director_ids):
                base = base.where(
                    col(Metadata.id).in_(
                        select(MetadataDirector.metadata_id).where(MetadataDirector.director_id == director_id)
                    )
                )
            for tag_id in unique_ids(tag_ids):
                base = base.where(
                    col(Metadata.id).in_(select(MetadataTag.metadata_id).where(MetadataTag.tag_id == tag_id))
                )
            for user_tag_id in unique_ids(user_tag_ids):
                base = base.where(
                    col(Metadata.id).in_(
                        select(MetadataUserTag.metadata_id).where(MetadataUserTag.user_tag_id == user_tag_id)
                    )
                )
            studio_names = await resolve_scalar_facet_names(session, Studio, studio_ids)
            if studio_ids and not studio_names:
                return [], 0
            if studio_names:
                base = base.where(col(Metadata.studio).in_(studio_names))
            publisher_names = await resolve_scalar_facet_names(session, Publisher, publisher_ids)
            if publisher_ids and not publisher_names:
                return [], 0
            if publisher_names:
                base = base.where(col(Metadata.publisher).in_(publisher_names))
            series_names = await resolve_scalar_facet_names(session, Series, series_ids)
            if series_ids and not series_names:
                return [], 0
            if series_names:
                base = base.where(col(Metadata.series).in_(series_names))
            if has_files is not None:
                base = base.where(_metadata_has_files_clause(has_files=has_files))
            if has_subtitle is True:
                base = base.where(_metadata_linked_file_exists(col(MediaFile.has_subtitle).is_(True)))
            elif has_subtitle is False:
                base = base.where(~_metadata_linked_file_exists(col(MediaFile.has_subtitle).is_(True)))
            if mosaic is not None:
                base = base.where(_metadata_linked_file_exists(col(MediaFile.mosaic) == mosaic))
            if uncensored is True:
                base = base.where(_metadata_linked_file_exists(_media_file_uncensored_predicate()))
            elif uncensored is False:
                base = base.where(~_metadata_linked_file_exists(_media_file_uncensored_predicate()))
            if definition is not None:
                base = base.where(_metadata_linked_file_exists(col(MediaFile.definition) == definition))
            if content_type is not None:
                base = base.where(_metadata_linked_file_exists(col(MediaFile.content_type) == content_type))
            if updated_before is not None:
                base = base.where(col(Metadata.updated_at) < updated_before)

            count_stmt = select(count()).select_from(base.subquery())
            total: int = (await session.exec(count_stmt)).one() or 0
            stmt = (
                base.order_by(_metadata_primary_order(sort_by, order), col(Metadata.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all()), total

    async def get_metadata(self, metadata_id: int) -> Metadata | None:
        async with self._session() as session:
            return await session.get(Metadata, metadata_id)

    async def get_metadata_by_number(self, number: str) -> Metadata | None:
        """大小写不敏感; 返回行保留库内原始大小写."""
        async with self._session() as session:
            stmt = select(Metadata).where(func.lower(Metadata.number) == number.lower())
            result = await session.exec(stmt)
            return result.first()

    async def upsert_metadata(
        self,
        number: str,
        *,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **kwargs: Unpack[MetadataFields],
    ) -> Metadata:
        """查重忽略大小写; 已存在时不改写 number 的原始大小写.
        ``actor_genders`` 只填 ``Actor.gender`` 空位, 不是 Metadata 列.
        """
        async with self._session() as session:
            stmt = select(Metadata).where(func.lower(Metadata.number) == number.lower())
            result = await session.exec(stmt)
            existing = result.first()
            if existing:
                for key, value in kwargs.items():
                    setattr(existing, key, value)
                existing.updated_at = _utcnow()
                session.add(existing)
                await session.flush()
                await clean_actor_names(session, existing, actor_genders)
                await apply_facet_rules_to_metadata(session, existing)
                await sync_metadata_facets(session, existing)
                await session.commit()
                await session.refresh(existing)
                return existing
            meta = Metadata(number=number, **kwargs)
            session.add(meta)
            await session.flush()
            await clean_actor_names(session, meta, actor_genders)
            await apply_facet_rules_to_metadata(session, meta)
            await sync_metadata_facets(session, meta)
            await session.commit()
            await session.refresh(meta)
            return meta

    async def update_metadata(
        self,
        metadata_id: int,
        *,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **updates: Unpack[MetadataFields],
    ) -> Metadata | None:
        """不存在返回 None."""
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return None
            # 显式赋值, 禁止 setattr; 字段集由 MetadataFields 与 Metadata 静态对齐.
            if "title" in updates:
                metadata.title = updates["title"]
            if "actors" in updates:
                metadata.actors = updates["actors"]
            if "studio" in updates:
                metadata.studio = updates["studio"]
            if "publisher" in updates:
                metadata.publisher = updates["publisher"]
            if "release" in updates:
                metadata.release = updates["release"]
            if "runtime" in updates:
                metadata.runtime = updates["runtime"]
            if "tags" in updates:
                metadata.tags = updates["tags"]
            if "series" in updates:
                metadata.series = updates["series"]
            if "plot" in updates:
                metadata.plot = updates["plot"]
            if "directors" in updates:
                metadata.directors = updates["directors"]
            if "poster_urls" in updates:
                metadata.poster_urls = updates["poster_urls"]
            if "thumb_urls" in updates:
                metadata.thumb_urls = updates["thumb_urls"]
            if "trailer_urls" in updates:
                metadata.trailer_urls = updates["trailer_urls"]
            if "extrafanart_urls" in updates:
                metadata.extrafanart_urls = updates["extrafanart_urls"]
            if "scores" in updates:
                metadata.scores = updates["scores"]
            if "external_ids" in updates:
                metadata.external_ids = updates["external_ids"]
            if "source_urls" in updates:
                metadata.source_urls = updates["source_urls"]
            if "field_sources" in updates:
                metadata.field_sources = updates["field_sources"]
            if "raw" in updates:
                metadata.raw = updates["raw"]
            metadata.updated_at = _utcnow()
            session.add(metadata)
            await session.flush()
            await clean_actor_names(session, metadata, actor_genders)
            await apply_facet_rules_to_metadata(session, metadata)
            await sync_metadata_facets(session, metadata)
            await session.commit()
            await session.refresh(metadata)
            return metadata

    async def delete_metadata(self, metadata_id: int) -> bool:
        """级联见 ``cascade_delete_metadata``. 不存在返回 False."""
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return False
            await cascade_delete_metadata(session, metadata)
            await session.commit()
            return True

    async def batch_delete_metadata(self, ids: list[int]) -> tuple[int, int]:
        """级联与 :meth:`delete_metadata` 一致, 单个事务. 不存在的 id 计入 missing."""
        async with self._session() as session:
            deleted = 0
            missing = 0
            for metadata_id in ids:
                metadata = await session.get(Metadata, metadata_id)
                if metadata is None:
                    missing += 1
                    continue
                await cascade_delete_metadata(session, metadata)
                deleted += 1
            await session.commit()
            return deleted, missing
