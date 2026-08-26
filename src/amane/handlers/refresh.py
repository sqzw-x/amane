from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..db import TaskType
from ..parsing import parse_file_info
from ..utils.oshash import compute_oshash_async
from ._common import iter_media_files, register_media_file
from .models import RefreshPayload, RefreshResult, ScanMode, ScrapePayload
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

logger = structlog.get_logger()


class RefreshHandler(TaskHandler[RefreshPayload, RefreshResult]):
    """以 Library 为单位, 按需扫描文件索引 (注册新文件/清理失效/回填 oshash) 并 fan-out SCRAPE."""

    def __init__(self, repo: Repository):
        super().__init__(payload_t=RefreshPayload, result_t=RefreshResult)
        self._repo = repo

    async def handle(self, payload: RefreshPayload) -> TaskResult[RefreshResult]:
        scan_dir = Path(payload.path)
        if not scan_dir.is_dir():
            return TaskResult(success=False, error=f"Not a directory: {payload.path}")

        library = await self._repo.get_library(payload.library_id)
        skip_patterns = [library.trailer_pattern, *(library.blacklist_patterns or [])] if library is not None else None

        added = removed = scrape = 0

        # 扫描文件并添加/清理数据库条目
        if payload.scan:
            disk_files = set(
                map(
                    str,
                    iter_media_files(
                        scan_dir,
                        recursive=payload.recursive if payload.recursive is not None else True,
                        patterns=payload.patterns,
                        skip_patterns=skip_patterns,
                    ),
                )
            )

            # 清理无效条目 (即数据库中存在但本地已不存在的)
            if ScanMode.remove in payload.scan:
                invalid = await self._repo.get_invalid(disk_files, library_id=payload.library_id)
                logger.info("remove invalid db entries", count=len(invalid))
                for media in invalid:
                    assert media.id is not None
                    await self._repo.delete_media_file(media.id)
                removed += len(invalid)

            # 添加新发现的文件
            valid = await self._repo.get_valid(disk_files)
            new_files = disk_files - {f.path for f in valid}
            if ScanMode.add in payload.scan and new_files:
                logger.info("new files discovered", path=payload.path, count=len(new_files))
                for path in new_files:
                    await register_media_file(self._repo, payload.library_id, Path(path))
                added += len(new_files)

        # 存量条目回填 oshash (仅补缺失, 供 ThePornDB 等指纹匹配站点使用)
        if payload.scan:
            backfilled = 0
            for f in await self._repo.list_media_files(library_id=payload.library_id, limit=None):
                if f.oshash is None:
                    media_id = f.id
                    assert media_id is not None
                    media_hash = await compute_oshash_async(Path(f.path))
                    if media_hash is not None:
                        await self._repo.update_media_file(media_id, oshash=media_hash)
                        backfilled += 1
            if backfilled:
                logger.info("oshash backfilled", count=backfilled)

        media_files = await self._repo.list_media_files(
            library_id=payload.library_id, status=payload.scrape, limit=None
        )

        # 提交 SCRAPE 任务: 只描述后继 (含媒体文件 ID), 由 worker 完成阶段统一创建.
        followups = []
        for f in media_files:
            parsed = parse_file_info(f.path)
            assert f.id is not None
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
