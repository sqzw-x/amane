"""RESCRAPE 任务 handler - 元数据级滚动补刮 (fan-out 到 SCRAPE).

与 RefreshHandler 同构: 批量任务只做"选择目标 + 下发子任务", 重活交给 SCRAPE
(逐条复用 per-site raw 快照, 非 force 仅补缺失站点, 聚合阶段重放当前配置,
因此也承担"配置变更后重跑生效"的用途).

content_type 不存表, 运行时推断: 有挂载文件用 parse_file_info (含路径关键词),
无文件退回 classify_number (番号模式). Metadata 是 ground truth, 不依赖文件存在.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..db import TaskType
from ..db.models import MetadataSortField, SortOrder
from ..parsing import infer_content_type
from .models import CacheKind, RescrapePayload, RescrapeResult, ScrapePayload
from .protocol import FollowupTask, TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..db.repository import Repository


class RescrapeHandler(TaskHandler[RescrapePayload, RescrapeResult]):
    """处理 RESCRAPE 任务 - 按 updated_at 滚动窗口为 Metadata 提交低优先 SCRAPE."""

    def __init__(self, repo: Repository) -> None:
        super().__init__(payload_t=RescrapePayload, result_t=RescrapeResult)
        self._repo = repo

    async def handle(self, payload: RescrapePayload) -> TaskResult[RescrapeResult]:
        updated_before = (
            datetime.now(UTC) - timedelta(days=payload.min_age_days) if payload.min_age_days is not None else None
        )
        items, _ = await self._repo.list_metadata(
            sort_by=MetadataSortField.UPDATED_AT,
            order=SortOrder.ASC,
            limit=payload.limit,
            updated_before=updated_before,
        )
        if not items:
            return TaskResult(success=True, result=RescrapeResult(submitted=0))

        identified = [(m, m.id) for m in items if m.id is not None]
        # 挂载文件仅用于 content_type 推断: 每个 metadata 取第一个文件即可 (同号文件类型一致).
        files = await self._repo.list_media_files(metadata_ids=[mid for _, mid in identified], limit=None)
        first_path_by_metadata: dict[int, str] = {}
        for f in files:
            if f.metadata_id is not None and f.metadata_id not in first_path_by_metadata:
                first_path_by_metadata[f.metadata_id] = f.path

        followups = []
        for meta, meta_id in identified:
            followups.append(
                FollowupTask(
                    key=f"scrape:{meta_id}",
                    task_type=TaskType.SCRAPE,
                    payload=ScrapePayload(
                        number=meta.number,
                        content_type=infer_content_type(meta.number, first_path_by_metadata.get(meta_id)),
                        use_cache={CacheKind.metadata, CacheKind.trans},
                    ).model_dump(mode="json"),
                    priority=-1,
                )
            )

        return TaskResult(
            success=True,
            result=RescrapeResult(submitted=len(identified)),
            followups=followups,
        )
