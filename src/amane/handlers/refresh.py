from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..db import TaskType
from ..library import MEDIA_EXTENSIONS, LibraryFileKind, LibraryScan
from ..parsing import parse_file_info
from ..utils.path import nfc_path
from ..utils.threads import path_exists, path_is_dir
from ._common import register_media_file, scan_library
from .models import RefreshPayload, RefreshResult, ScanMode, ScrapePayload
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()

_WALK_LOG_EVERY = 500


class RefreshHandler(TaskHandler[RefreshPayload, RefreshResult]):
    def __init__(self, repo: Repository, media_extensions: Sequence[str] | None = None):
        super().__init__(payload_t=RefreshPayload, result_t=RefreshResult)
        self._repo = repo
        self._media_extensions = frozenset(media_extensions) if media_extensions else MEDIA_EXTENSIONS

    async def handle(self, payload: RefreshPayload) -> TaskResult[RefreshResult]:
        scan_dir = Path(payload.path)
        if not await path_is_dir(scan_dir):
            return TaskResult(success=False, error=f"Not a directory: {payload.path}")

        library = await self._repo.get_library(payload.library_id)
        scan = LibraryScan(
            patterns=payload.patterns,
            trailer_pattern=library.trailer_pattern if library is not None else None,
            blacklist_patterns=library.blacklist_patterns if library is not None else None,
            min_file_size=library.min_file_size if library is not None else 0,
            media_extensions=self._media_extensions,
        )

        added = removed = scrape = 0

        existing = await self._repo.list_media_files(library_id=payload.library_id, limit=None)
        existing_by_path = {nfc_path(f.path): f for f in existing}

        if payload.scan:
            want_add = ScanMode.add in payload.scan
            want_remove = ScanMode.remove in payload.scan
            # add 与 remove 同时启用: 一次遍历收集 seen, 再做集合差.
            # 仅 remove: 不对整树 glob, 对库内记录逐条 exists (O(索引) 而非 O(磁盘树)).
            seen: set[str] | None = set() if want_add and want_remove else None

            # 扫描磁盘并注册未见过的媒体.
            if want_add:
                logger.info("scan walking started", path=payload.path)
                walked = 0
                hits = await scan_library(
                    scan_dir, recursive=payload.recursive if payload.recursive is not None else True, scan=scan
                )
                for hit in hits:
                    if hit.kind is not LibraryFileKind.MEDIA:
                        continue
                    file_path = hit.path
                    path_key = nfc_path(str(file_path))
                    walked += 1
                    if seen is not None:
                        seen.add(path_key)
                    if walked % _WALK_LOG_EVERY == 0:
                        logger.info("scan walking", path=payload.path, seen=walked, added=added)
                    if path_key not in existing_by_path:
                        media = await register_media_file(self._repo, payload.library_id, file_path)
                        existing_by_path[path_key] = media
                        added += 1
                if added:
                    logger.info("new files discovered", path=payload.path, count=added)

            # 删除索引中已不存在的记录.
            if want_remove:
                if seen is not None:
                    missing = [f for f in existing if nfc_path(f.path) not in seen]
                else:
                    missing = []
                    for f in existing:
                        still_there = await path_exists(Path(f.path))
                        if not still_there:
                            missing.append(f)
                if missing:
                    logger.info("remove invalid db entries", count=len(missing))
                    for media in missing:
                        assert media.id is not None
                        await self._repo.delete_media_file(media.id)
                    removed += len(missing)

        media_files = await self._repo.list_media_files(
            library_id=payload.library_id, status=payload.scrape, limit=None
        )

        # 扇出 SCRAPE; 只描述后继, 由完成事务创建.
        followups = []
        for f in media_files:
            parsed = parse_file_info(f.path)
            assert f.id is not None
            assert parsed.number is not None
            followups.append(
                FollowupTask(
                    key=f"scrape:{f.id}",
                    task_type=TaskType.SCRAPE,
                    payload=ScrapePayload(
                        media_file_id=f.id,
                        number=parsed.number,
                        content_type=parsed.content_type,
                        use_cache=payload.use_cache,
                    ).model_dump(mode="json"),
                )
            )
            scrape += 1

        logger.info("scan completed", path=payload.path, added=added, removed=removed, scrape=scrape)

        return TaskResult(True, result=RefreshResult(added=added, removed=removed, scrape=scrape), followups=followups)
