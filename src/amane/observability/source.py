"""一次来源调用的边界: catch + 写入站点 outcome. 爬虫 / 插件不 import 本模块."""

from collections.abc import Awaitable, Callable

from ..net.errors import FailureReason, SourceError
from .models import SiteOutcomeKind
from .recorder import current


async def invoke_source[T](source_id: str, fetch: Callable[[], Awaitable[T | None]]) -> T | None:
    """把一次来源 fetch 记进当前 Recorder. 其它 Exception 记 unexpected, 不使整任务失败."""
    rec = current()
    try:
        result = await fetch()
    except SourceError as exc:
        rec.record_site_outcome(
            site=source_id,
            outcome=SiteOutcomeKind.FAILED,
            reason=exc.reason,
            http_status=exc.http_status,
            detail=exc.detail,
        )
        # SourceError 是刻意上报的原因, 不冒泡到任务; 若不在此记日志, 原因只剩 summary.json 可见.
        rec.warning("source failed", source=source_id, reason=exc.reason.value, detail=exc.detail)
        return None
    except Exception:
        rec.exception("source fetch failed", source=source_id)
        rec.record_site_outcome(site=source_id, outcome=SiteOutcomeKind.FAILED, reason=FailureReason.UNEXPECTED)
        return None
    if result is None:
        rec.record_site_outcome(site=source_id, outcome=SiteOutcomeKind.FAILED, reason=FailureReason.NO_USABLE_METADATA)
        return None
    rec.record_site_outcome(site=source_id, outcome=SiteOutcomeKind.OK)
    return result
