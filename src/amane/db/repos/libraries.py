from collections.abc import Sequence
from typing import Unpack

from sqlmodel import col, select

from amane.enums import DownloadableResource, LibraryAutomation, MoveMode
from amane.organize.path_templates import CD_SUFFIX_TEMPLATE_DEFAULT, validate_cd_suffix_template
from amane.utils.extensions import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    normalize_subtitle_extensions,
    validate_blacklist_pattern,
    validate_trailer_pattern,
)

from ..models import Library, MediaFile
from ..repo_types import LibraryUpdates
from .base import RepositoryMixinBase


class LibrariesRepoMixin(RepositoryMixinBase):
    async def create_library(
        self,
        name: str,
        path: str,
        automation: LibraryAutomation = LibraryAutomation.SCRAPE,
        recursive: bool = True,
        patterns: list[str] | None = None,
        move_mode: MoveMode = MoveMode.MOVE,
        video_template: str = "{studio}/{number}/{number}.{ext}",
        cd_suffix_template: str | None = None,
        thumb_template: str | None = None,
        poster_template: str | None = None,
        fanart_template: str | None = None,
        extrafanart_template: str | None = None,
        nfo_template: str | None = None,
        trailer_template: str | None = None,
        subtitle_template: str | None = None,
        subtitle_extensions: list[str] | None = None,
        write_nfo: bool = True,
        copy_resources: list[DownloadableResource] | None = None,
        trailer_pattern: str | None = None,
        blacklist_patterns: list[str] | None = None,
    ) -> Library:
        async with self._session() as session:
            lib = Library(
                name=name,
                path=path,
                automation=automation,
                recursive=recursive,
                patterns=patterns if patterns is not None else [],
                move_mode=move_mode,
                video_template=video_template,
                cd_suffix_template=validate_cd_suffix_template(
                    cd_suffix_template if cd_suffix_template is not None else CD_SUFFIX_TEMPLATE_DEFAULT
                ),
                thumb_template=thumb_template,
                poster_template=poster_template,
                fanart_template=fanart_template,
                extrafanart_template=extrafanart_template,
                nfo_template=nfo_template,
                trailer_template=trailer_template,
                subtitle_template=subtitle_template,
                subtitle_extensions=normalize_subtitle_extensions(
                    list(subtitle_extensions) if subtitle_extensions is not None else list(DEFAULT_SUBTITLE_EXTENSIONS)
                ),
                write_nfo=write_nfo,
                copy_resources=list(copy_resources) if copy_resources is not None else list(DownloadableResource),
                trailer_pattern=validate_trailer_pattern(
                    trailer_pattern if trailer_pattern is not None else DEFAULT_TRAILER_PATTERN
                ),
                blacklist_patterns=[validate_blacklist_pattern(p) for p in (blacklist_patterns or [])],
            )
            session.add(lib)
            await session.commit()
            await session.refresh(lib)
            return lib

    async def list_libraries(self, watch_only: bool = False) -> list[Library]:
        async with self._session() as session:
            stmt = select(Library)
            if watch_only:
                stmt = stmt.where(Library.automation != LibraryAutomation.NONE)
            result = await session.exec(stmt)
            return list(result.all())

    async def get_library(self, library_id: int) -> Library | None:
        async with self._session() as session:
            return await session.get(Library, library_id)

    async def get_library_names(self, library_ids: Sequence[int]) -> dict[int, str]:
        """批量取 Library 名 (任务列表标题投影用); 不存在的 id 不出现在结果中."""
        ids = list(dict.fromkeys(int(i) for i in library_ids if i))
        if not ids:
            return {}
        async with self._session() as session:
            rows = (await session.exec(select(Library.id, Library.name).where(col(Library.id).in_(ids)))).all()
            return {int(i): str(n) for i, n in rows if i is not None}

    async def get_library_for_path(self, file_path: str) -> Library | None:
        """通过最长前缀匹配找到文件路径所属的 Library (仅迁移/兜底用)."""
        best: Library | None = None
        for lib in await self.list_libraries():
            if file_path.startswith(lib.path) and (best is None or len(lib.path) > len(best.path)):
                best = lib
        return best

    async def delete_library(self, library_id: int) -> int:
        """删除媒体库并级联删除其下所有 MediaFile 记录.

        MediaFile.library_id 是非空 FK, 删库必须同时清理归属文件, 否则留下悬空引用
        (见 delete_metadata 的同类应用层级联). 返回被级联删除的 MediaFile 数量.
        """
        async with self._session() as session:
            lib = await session.get(Library, library_id)
            if lib is None:
                return 0
            stmt = select(MediaFile).where(MediaFile.library_id == library_id)
            result = await session.exec(stmt)
            media_files = list(result.all())
            for mf in media_files:
                await session.delete(mf)
            # 先 flush 子表删除, 确保在删除父 Library 前 FK 引用已清除
            # (MediaFile/Library 未声明 ORM relationship, UoW 不会自动排序删除顺序)
            await session.flush()
            await session.delete(lib)
            await session.commit()
            return len(media_files)

    async def update_library(self, library_id: int, **updates: Unpack[LibraryUpdates]) -> Library | None:
        async with self._session() as session:
            lib = await session.get(Library, library_id)
            if lib is None:
                return None
            # 显式赋值: 字段名与类型由 LibraryUpdates(TypedDict) 与 Library 静态保证一致.
            if "name" in updates:
                lib.name = updates["name"]
            if "path" in updates:
                lib.path = updates["path"]
            if "automation" in updates:
                lib.automation = updates["automation"]
            if "recursive" in updates:
                lib.recursive = updates["recursive"]
            if "patterns" in updates:
                patterns = updates["patterns"]
                lib.patterns = patterns if patterns is not None else []
            if "move_mode" in updates:
                lib.move_mode = updates["move_mode"]
            if "video_template" in updates:
                lib.video_template = updates["video_template"]
            if "cd_suffix_template" in updates:
                lib.cd_suffix_template = validate_cd_suffix_template(updates["cd_suffix_template"])
            if "thumb_template" in updates:
                lib.thumb_template = updates["thumb_template"]
            if "poster_template" in updates:
                lib.poster_template = updates["poster_template"]
            if "fanart_template" in updates:
                lib.fanart_template = updates["fanart_template"]
            if "extrafanart_template" in updates:
                lib.extrafanart_template = updates["extrafanart_template"]
            if "nfo_template" in updates:
                lib.nfo_template = updates["nfo_template"]
            if "trailer_template" in updates:
                lib.trailer_template = updates["trailer_template"]
            if "subtitle_template" in updates:
                lib.subtitle_template = updates["subtitle_template"]
            if "subtitle_extensions" in updates:
                extensions = updates["subtitle_extensions"]
                lib.subtitle_extensions = normalize_subtitle_extensions(
                    list(extensions) if extensions is not None else []
                )
            if "write_nfo" in updates:
                lib.write_nfo = updates["write_nfo"]
            if "copy_resources" in updates:
                resources = updates["copy_resources"]
                lib.copy_resources = resources if resources is not None else []
            if "trailer_pattern" in updates:
                lib.trailer_pattern = validate_trailer_pattern(updates["trailer_pattern"])
            if "blacklist_patterns" in updates:
                patterns = updates["blacklist_patterns"]
                lib.blacklist_patterns = [validate_blacklist_pattern(p) for p in (patterns or [])]
            session.add(lib)
            await session.commit()
            await session.refresh(lib)
            return lib
