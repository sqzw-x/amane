from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Unpack, cast

from sqlalchemy import func, or_, text
from sqlalchemy.sql.functions import count
from sqlmodel import col, select

from ...enums import ActorGender, MetadataField
from ...parsing import ContentType, Mosaic
from ...utils.text import normalize_long_text
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
    has_subtitle_predicate,
)
from ..repo_types import (
    MetadataFields,
    WriteMode,
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
    project_derived_columns,
    resolve_scalar_facet_names,
    sync_metadata_facets,
    unique_ids,
)


def _normalize_text_fields(fields: MetadataFields) -> MetadataFields:
    """长文本列在落库前归一 (与聚合输出同一函数, 幂等).

    覆盖面是全部写库路径: 首刮 / 补刮 / merge / REST PATCH / Agent 工具都经这两个写方法.
    """
    plot = fields.get("plot")
    if not isinstance(plot, str):
        return fields
    return {**fields, "plot": normalize_long_text(plot)}


# 锁字段 (MetadataField) ↔ Metadata 列名; 仅 extrafanart / score 两名不同.
_LOCK_FIELD_COLUMN: dict[MetadataField, str] = {
    MetadataField.TITLE: "title",
    MetadataField.PLOT: "plot",
    MetadataField.ACTORS: "actors",
    MetadataField.DIRECTORS: "directors",
    MetadataField.TAGS: "tags",
    MetadataField.SERIES: "series",
    MetadataField.RELEASE: "release",
    MetadataField.RUNTIME: "runtime",
    MetadataField.PUBLISHER: "publisher",
    MetadataField.STUDIO: "studio",
    MetadataField.POSTER_URLS: "poster_urls",
    MetadataField.THUMB_URLS: "thumb_urls",
    MetadataField.TRAILER_URLS: "trailer_urls",
    MetadataField.EXTRAFANART: "extrafanart_urls",
    MetadataField.SCORE: "scores",
}
_LOCK_COLUMN_FIELD: dict[str, MetadataField] = {column: field for field, column in _LOCK_FIELD_COLUMN.items()}


def _locked_fields_of(meta: Metadata) -> set[MetadataField]:
    """库内锁定集合; 非法存量值忽略."""
    locked: set[MetadataField] = set()
    for name in meta.locked_fields or []:
        try:
            locked.add(MetadataField(name))
        except ValueError:
            continue
    return locked


def _filter_locked(
    fields: MetadataFields, locked: set[MetadataField], existing_sources: Mapping[str, str]
) -> MetadataFields:
    """AUTO 写入: 跳过锁定列, ``field_sources`` 保留锁定字段的既有来源."""
    if not locked:
        return fields
    locked_columns = {_LOCK_FIELD_COLUMN[field] for field in locked}
    filtered = cast("MetadataFields", {key: value for key, value in fields.items() if key not in locked_columns})
    sources = filtered.get("field_sources")
    if sources is not None:
        preserved = {key: value for key, value in existing_sources.items() if key in locked}
        merged = {**preserved, **{key: value for key, value in sources.items() if key not in locked}}
        filtered = cast("MetadataFields", {**filtered, "field_sources": merged})
    return filtered


def _merge_auto_locks(fields: MetadataFields, locked: set[MetadataField]) -> list[str]:
    """MANUAL 写入: 把本次写入的可锁列并入锁定集."""
    written = {_LOCK_COLUMN_FIELD[key] for key in fields if key in _LOCK_COLUMN_FIELD}
    return [str(field) for field in MetadataField if field in locked | written]


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
        vr: bool | None = None,
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
            if vr is not None:
                base = base.where(col(Metadata.vr).is_(vr))
            if has_subtitle is True:
                base = base.where(_metadata_linked_file_exists(has_subtitle_predicate()))
            elif has_subtitle is False:
                base = base.where(~_metadata_linked_file_exists(has_subtitle_predicate()))
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
        自动刮削写入; 锁定行为见 docs/dev/data-model.md.
        ``actor_genders`` 只填 ``Actor.gender`` 空位, 不是 Metadata 列.
        """
        fields = _normalize_text_fields(kwargs)
        async with self._session() as session:
            stmt = select(Metadata).where(func.lower(Metadata.number) == number.lower())
            result = await session.exec(stmt)
            existing = result.first()
            if existing:
                fields = _filter_locked(fields, _locked_fields_of(existing), existing.field_sources or {})
                for key, value in fields.items():
                    setattr(existing, key, value)
                existing.updated_at = _utcnow()
                session.add(existing)
                await session.flush()
                await clean_actor_names(session, existing, actor_genders)
                await apply_facet_rules_to_metadata(session, existing)
                project_derived_columns(existing)
                await sync_metadata_facets(session, existing)
                await session.commit()
                await session.refresh(existing)
                return existing
            meta = Metadata(number=number, **fields)
            session.add(meta)
            await session.flush()
            await clean_actor_names(session, meta, actor_genders)
            await apply_facet_rules_to_metadata(session, meta)
            project_derived_columns(meta)
            await sync_metadata_facets(session, meta)
            await session.commit()
            await session.refresh(meta)
            return meta

    async def update_metadata(
        self,
        metadata_id: int,
        *,
        mode: WriteMode = WriteMode.AUTO,
        actor_genders: Mapping[str, ActorGender] | None = None,
        **updates: Unpack[MetadataFields],
    ) -> Metadata | None:
        """不存在返回 None; 写入策略见 WriteMode."""
        updates = _normalize_text_fields(updates)
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return None
            locked = _locked_fields_of(metadata)
            if mode is WriteMode.AUTO:
                updates = _filter_locked(updates, locked, metadata.field_sources or {})
            else:
                metadata.locked_fields = _merge_auto_locks(updates, locked)
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
            project_derived_columns(metadata)
            await sync_metadata_facets(session, metadata)
            await session.commit()
            await session.refresh(metadata)
            return metadata

    async def set_metadata_locks(self, metadata_id: int, fields: Collection[MetadataField]) -> Metadata | None:
        """整体替换锁定字段集合, 不修改 ``updated_at``. 不存在返回 None."""
        async with self._session() as session:
            metadata = await session.get(Metadata, metadata_id)
            if metadata is None:
                return None
            wanted = set(fields)
            metadata.locked_fields = [str(field) for field in MetadataField if field in wanted]
            session.add(metadata)
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
