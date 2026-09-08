from fastapi import APIRouter, Response

from ..deps import RuntimeDep
from ..models.webhooks import CloudDriveNotifyRequest

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/clouddrive", status_code=204)
async def clouddrive_notify(req: CloudDriveNotifyRequest, runtime: RuntimeDep) -> Response:
    """CloudDrive file_system_watcher 回调. 鉴权与其它 /api 相同 (Bearer)."""
    if runtime.watcher_service is None or not req.data:
        return Response(status_code=204)
    runtime.watcher_service.submit_clouddrive([item.to_change() for item in req.data])
    return Response(status_code=204)
