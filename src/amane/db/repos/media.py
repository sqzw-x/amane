from collections.abc import Iterable, Sequence
from typing import NamedTuple, Unpack

from sqlalchemy import func, or_
from sqlalchemy.sql.functions import count
from sqlmodel import col, select

from ...parsing import ContentType, FilePhase, FilePhaseSummary, Mosaic, file_phase_from_path, summarize_file_phases
from ...utils.path import nfc_path
from ..models import MediaFile, MediaFileStatus, MediaSortField, SortOrder
from ..repo_types import _MEDIA_SORT_COLUMNS, MediaFileUpdates, _apply_media_phase_filters, _order_clause, _utcnow
from .base import RepositoryMixinBase

# SQLite 绑定变量上限. 500 给语句里其它占位留余量.
SQL_IN_CHUNK_SIZE = 500


def _apply_path_phase(media: MediaFile) -> None:
    """path 是真值, 相位列是投影; 创建与改 path 时必须回填."""
    phase = file_phase_from_path(media.path)
    media.content_type = phase["content_type"]
    media.mosaic = phase["mosaic"]
    media.has_subtitle = phase["has_subtitle"]
    media.definition = phase["definition"]


def file_phase_of(media: MediaFile) -> FilePhase:
    """从已落库列组装, 不再解析 path."""
    return FilePhase(
        content_type=media.content_type,
        mosaic=media.mosaic,
        has_subtitle=media.has_subtitle,
        definition=media.definition,
    )


class MetadataFilesSummary(NamedTuple):
    file_count: int
    phase: FilePhaseSummary


class MediaRepoMixin(RepositoryMixinBase):
    async def create_media_file(self, library_id: int, **updates: Unpack[MediaFileUpdates]) -> MediaFile:
        path = updates.get("path")
        if path is not None:
            updates["path"] = nfc_path(path)
        async with self._session() as session:
            media = MediaFile(library_id=library_id, **updates)
            _apply_path_phase(media)
            session.add(media)
            await session.commit()
            await session.refresh(media)
            return media

    async def get_media_file(self, media_id: int) -> MediaFile | None:
        async with self._session() as session:
            return await session.get(MediaFile, media_id)

    async def get_media_file_by_path(self, path: str) -> MediaFile | None:
        async with self._session() as session:
            stmt = select(MediaFile).where(MediaFile.path == nfc_path(path))
            result = await session.exec(stmt)
            return result.first()

    async def get_valid(self, disk_paths: Iterable[str]) -> Sequence[MediaFile]:
        """路径按 SQL_IN_CHUNK_SIZE 分批 IN, 避开绑定变量上限. 比较前统一 NFC."""
        paths = [nfc_path(p) for p in disk_paths]
        if not paths:
            return []
        found: list[MediaFile] = []
        async with self._session() as session:
            for offset in range(0, len(paths), SQL_IN_CHUNK_SIZE):
                chunk = paths[offset : offset + SQL_IN_CHUNK_SIZE]
                stmt = select(MediaFile).where(col(MediaFile.path).in_(chunk))
                result = await session.exec(stmt)
                found.extend(result.all())
        return found

    async def get_invalid(self, disk_paths: Iterable[str], library_id: int | None = None) -> Sequence[MediaFile]:
        """library_id 须收窄到单库, 否则扫描 A 会误删 B.
        在 Python 做集合差, 禁止 SQL NOT IN: 分批 NOT IN 会把其它批里真实存在的路径误判为失效.
        """
        if library_id is None and not disk_paths:
            return []
        files = await self.list_media_files(library_id=library_id, limit=None)
        disk = frozenset(nfc_path(p) for p in disk_paths)
        if not disk:
            return files
        return [f for f in files if nfc_path(f.path) not in disk]

    async def list_media_files(
        self,
        status: Iterable[MediaFileStatus] | None = None,
        limit: int | None = 50,
        offset: int = 0,
        search: str | None = None,
        library_id: int | None = None,
        sort_by: MediaSortField = MediaSortField.UPDATED_AT,
        order: SortOrder = SortOrder.DESC,
        metadata_ids: Sequence[int] | None = None,
        *,
        has_subtitle: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
    ) -> list[MediaFile]:
        """limit None 不分页."""
        async with self._session() as session:
            stmt = select(MediaFile)
            if status is not None:
                stmt = stmt.where(col(MediaFile.status).in_(status))
            if library_id is not None:
                stmt = stmt.where(col(MediaFile.library_id) == library_id)
            if metadata_ids is not None:
                stmt = stmt.where(col(MediaFile.metadata_id).in_(metadata_ids))
            if search:
                pattern = f"%{search}%"
                stmt = stmt.where(or_(col(MediaFile.path).ilike(pattern), col(MediaFile.number).ilike(pattern)))
            stmt = _apply_media_phase_filters(
                stmt,
                has_subtitle=has_subtitle,
                mosaic=mosaic,
                uncensored=uncensored,
                definition=definition,
                content_type=content_type,
            )
            # 次级排序键 id 保证分页稳定.
            stmt = (
                stmt.order_by(_order_clause(_MEDIA_SORT_COLUMNS[sort_by], order), col(MediaFile.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def count_media_files(
        self,
        status: Iterable[MediaFileStatus] | None = None,
        search: str | None = None,
        library_id: int | None = None,
        *,
        has_subtitle: bool | None = None,
        mosaic: Mosaic | None = None,
        uncensored: bool | None = None,
        definition: str | None = None,
        content_type: ContentType | None = None,
    ) -> int:
        async with self._session() as session:
            base = select(MediaFile)
            if status is not None:
                base = base.where(col(MediaFile.status).in_(status))
            if library_id is not None:
                base = base.where(col(MediaFile.library_id) == library_id)
            if search:
                pattern = f"%{search}%"
                base = base.where(or_(col(MediaFile.path).ilike(pattern), col(MediaFile.number).ilike(pattern)))
            base = _apply_media_phase_filters(
                base,
                has_subtitle=has_subtitle,
                mosaic=mosaic,
                uncensored=uncensored,
                definition=definition,
                content_type=content_type,
            )
            stmt = select(count()).select_from(base.subquery())
            result = await session.exec(stmt)
            return result.one() or 0

    async def update_media_file(self, media_id: int, **updates: Unpack[MediaFileUpdates]) -> MediaFile | None:
        async with self._session() as session:
            media = await session.get(MediaFile, media_id)
            if media is None:
                return None
            # 显式赋值, 禁止 setattr; 字段集由 MediaFileUpdates 与 MediaFile 静态对齐.
            if "path" in updates:
                media.path = nfc_path(updates["path"])
                _apply_path_phase(media)
            if "number" in updates:
                media.number = updates["number"]
            if "oshash" in updates:
                media.oshash = updates["oshash"]
            if "size" in updates:
                media.size = updates["size"]
            if "duration" in updates:
                media.duration = updates["duration"]
            if "codec" in updates:
                media.codec = updates["codec"]
            if "status" in updates:
                media.status = updates["status"]
            if "metadata_id" in updates:
                media.metadata_id = updates["metadata_id"]
            media.updated_at = _utcnow()
            session.add(media)
            await session.commit()
            await session.refresh(media)
            return media

    async def delete_media_file(self, media_id: int) -> bool:
        async with self._session() as session:
            media = await session.get(MediaFile, media_id)
            if media is None:
                return False
            await session.delete(media)
            await session.commit()
            return True

    async def count_media_by_metadata_ids(self, metadata_ids: Sequence[int]) -> dict[int, int]:
        """无关联的 id 不出现在结果中."""
        ids = [i for i in metadata_ids if i]
        if not ids:
            return {}
        async with self._session() as session:
            stmt = (
                select(col(MediaFile.metadata_id), func.count())
                .where(col(MediaFile.metadata_id).in_(ids))
                .group_by(col(MediaFile.metadata_id))
            )
            result = await session.exec(stmt)
            return {mid: n for mid, n in result.all() if mid is not None}

    async def summarize_media_by_metadata_ids(self, metadata_ids: Sequence[int]) -> dict[int, MetadataFilesSummary]:
        """无关联的 id 不出现."""
        ids = [i for i in metadata_ids if i]
        if not ids:
            return {}
        async with self._session() as session:
            stmt = select(MediaFile).where(col(MediaFile.metadata_id).in_(ids))
            result = await session.exec(stmt)
            grouped: dict[int, list[FilePhase]] = {}
            for media in result.all():
                if media.metadata_id is None:
                    continue
                grouped.setdefault(media.metadata_id, []).append(file_phase_of(media))
            return {
                mid: MetadataFilesSummary(file_count=len(phases), phase=summarize_file_phases(phases))
                for mid, phases in grouped.items()
            }

    async def get_media_by_metadata_id(self, metadata_id: int) -> list[MediaFile]:
        async with self._session() as session:
            stmt = select(MediaFile).where(MediaFile.metadata_id == metadata_id)
            result = await session.exec(stmt)
            return list(result.all())
