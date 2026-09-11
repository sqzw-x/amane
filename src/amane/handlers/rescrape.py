"""按 updated_at 选取目标并扇出刮削. 影片 content_type 不存表, 运行时按路径或番号推断.
Metadata / Actor 各自判定, 不依赖挂载文件是否存在.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..db import ActorSortField, TaskType
from ..db.models import MetadataSortField, SortOrder
from ..parsing import infer_content_type
from .models import (
    ActorScrapePayload,
    CacheKind,
    RescrapePayload,
    RescrapeResult,
    RescrapeTarget,
    ScrapePayload,
)
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository

_USE_CACHE = {CacheKind.metadata, CacheKind.trans}


class RescrapeHandler(TaskHandler[RescrapePayload, RescrapeResult]):
    def __init__(self, repo: Repository) -> None:
        super().__init__(payload_t=RescrapePayload, result_t=RescrapeResult)
        self._repo = repo

    async def handle(self, payload: RescrapePayload) -> TaskResult[RescrapeResult]:
        updated_before = (
            datetime.now(UTC) - timedelta(days=payload.min_age_days) if payload.min_age_days is not None else None
        )
        followups: list[FollowupTask] = []
        metadata_count = 0
        actor_count = 0

        if RescrapeTarget.metadata in payload.targets:
            meta_followups = await self._metadata_followups(payload.limit, updated_before)
            metadata_count = len(meta_followups)
            followups.extend(meta_followups)

        if RescrapeTarget.actor in payload.targets:
            actor_followups = await self._actor_followups(payload.limit, updated_before)
            actor_count = len(actor_followups)
            followups.extend(actor_followups)

        return TaskResult(
            success=True,
            result=RescrapeResult(submitted=metadata_count + actor_count, metadata=metadata_count, actors=actor_count),
            followups=followups,
        )

    async def _metadata_followups(self, limit: int, updated_before: datetime | None) -> list[FollowupTask]:
        items, _ = await self._repo.list_metadata(
            sort_by=MetadataSortField.UPDATED_AT,
            order=SortOrder.ASC,
            limit=limit,
            updated_before=updated_before,
        )
        identified = [(m, m.id) for m in items if m.id is not None]
        if not identified:
            return []

        # 挂载文件仅用于 content_type 推断: 每个 metadata 取第一个文件即可 (同号文件类型一致).
        files = await self._repo.list_media_files(metadata_ids=[mid for _, mid in identified], limit=None)
        first_path_by_metadata: dict[int, str] = {}
        for f in files:
            if f.metadata_id is not None and f.metadata_id not in first_path_by_metadata:
                first_path_by_metadata[f.metadata_id] = f.path

        return [
            FollowupTask(
                key=f"scrape:{meta_id}",
                task_type=TaskType.SCRAPE,
                payload=ScrapePayload(
                    number=meta.number,
                    content_type=infer_content_type(meta.number, first_path_by_metadata.get(meta_id)),
                    use_cache=_USE_CACHE,
                ).model_dump(mode="json"),
                priority=-1,
            )
            for meta, meta_id in identified
        ]

    async def _actor_followups(self, limit: int, updated_before: datetime | None) -> list[FollowupTask]:
        actors = await self._repo.list_actors(
            sort_by=ActorSortField.UPDATED_AT,
            limit=limit,
            updated_before=updated_before,
        )
        return [
            FollowupTask(
                key=f"actor-scrape:{actor_id}",
                task_type=TaskType.ACTOR_SCRAPE,
                payload=ActorScrapePayload(actor_id=actor_id, use_cache=_USE_CACHE).model_dump(mode="json"),
                priority=-1,
            )
            for actor_id in (actor.id for actor in actors)
            if actor_id is not None
        ]
