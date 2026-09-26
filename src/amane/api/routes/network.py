"""来源连通性探测. 不进入任务队列: 结果要立刻回到用户手里, 也不该占用 worker 名额."""

from __future__ import annotations

from fastapi import APIRouter

from ...crawlers.connectivity import ConnectivityChecker, SourceCheck
from ..deps import RuntimeDep
from ..models import ConnectivityCheckRequest, ConnectivityItemResponse, ConnectivityReportResponse

router = APIRouter(prefix="/network", tags=["network"])


def _item(check: SourceCheck) -> ConnectivityItemResponse:
    outcome = check.outcome
    return ConnectivityItemResponse(
        source_id=check.source_id,
        name=check.name,
        kind=check.kind,
        status=outcome.status,
        url=outcome.url,
        http_status=outcome.http_status,
        reason=outcome.reason,
        skip_reason=outcome.skip_reason,
        detail=outcome.detail,
        elapsed_ms=check.elapsed_ms,
    )


@router.post("/check", response_model=ConnectivityReportResponse)
async def check_connectivity(
    runtime: RuntimeDep,
    req: ConnectivityCheckRequest | None = None,
) -> ConnectivityReportResponse:
    """探测来源连通性.

    单个来源失败只体现在自己的条目里, 端点始终 200 — 与 ``GET /api/system/release`` 同一取向:
    探测结果本身就是响应体, 用 5xx 表达「上游不通」会让前端无法展示逐来源原因.
    """
    checker = ConnectivityChecker(runtime.factory, runtime.config.hot, runtime.plugin_manager)
    checks = await checker.check(req.source_ids if req is not None else None)
    return ConnectivityReportResponse(items=[_item(check) for check in checks])
