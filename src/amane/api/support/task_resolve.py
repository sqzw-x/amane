"""scan/organize 由 library_id 派生 path/recursive/patterns."""

from typing import TYPE_CHECKING, assert_never

from ...db.models import TaskType
from ...handlers import (
    ActorScrapePayload,
    ArchivePayload,
    CleanupPayload,
    OrganizePayload,
    R18ImportPayload,
    RefreshPayload,
    RescrapePayload,
    ScrapePayload,
    UpscalePayload,
)
from ..models import (
    ActorScrapeSubmission,
    ArchiveSubmission,
    CleanupSubmission,
    OrganizeSubmission,
    R18ImportSubmission,
    RefreshSubmission,
    RescrapeSubmission,
    ScrapeSubmission,
    TaskSubmission,
    UpscaleSubmission,
)

if TYPE_CHECKING:
    from ...db.repository import Repository

ResolvedPayload = (
    RefreshPayload
    | ScrapePayload
    | OrganizePayload
    | ArchivePayload
    | CleanupPayload
    | UpscalePayload
    | R18ImportPayload
    | ActorScrapePayload
    | RescrapePayload
)


async def resolve_submission(req: TaskSubmission, repo: Repository) -> tuple[TaskType, ResolvedPayload]:
    match req:
        case RefreshSubmission():
            await req.resolve(repo)
            return TaskType.REFRESH, req
        case OrganizeSubmission():
            await req.resolve(repo)
            return TaskType.ORGANIZE, req
        case ArchiveSubmission():
            await req.resolve(repo)
            return TaskType.ARCHIVE, req
        case ScrapeSubmission():
            return TaskType.SCRAPE, await req.resolve(repo)
        case CleanupSubmission():
            return TaskType.CLEANUP, CleanupPayload(
                remove_missing_files=req.remove_missing_files,
                remove_unreferenced_resources=req.remove_unreferenced_resources,
            )
        case UpscaleSubmission():
            return TaskType.UPSCALE, UpscalePayload(
                max_dim_threshold=req.max_dim_threshold, max_bytes_threshold=req.max_bytes_threshold, limit=req.limit
            )
        case R18ImportSubmission():
            return TaskType.R18_IMPORT, R18ImportPayload(force=req.force)
        case ActorScrapeSubmission():
            return TaskType.ACTOR_SCRAPE, ActorScrapePayload(actor_id=req.actor_id, use_cache=req.use_cache)
        case RescrapeSubmission():
            return TaskType.RESCRAPE, RescrapePayload(limit=req.limit, min_age_days=req.min_age_days)
        case _:
            assert_never(req)
