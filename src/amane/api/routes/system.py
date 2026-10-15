"""桌面契约 / 重启 / 版本检查. 不进入任务队列."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request, Response

from ...release import is_newer
from ...version import get_version
from ..deps import ConfigDep, RuntimeDep
from ..models import DesktopResponse, ReleaseResponse

router = APIRouter(prefix="/system", tags=["system"])

# 与 amane.server.EXIT_RESTART 一致; 本模块不 import server, 以免与 create_app 成环.
EXIT_RESTART = 3
_SHUTDOWN_DELAY_S = 0.3


async def _trigger_shutdown(app: FastAPI) -> None:
    await asyncio.sleep(_SHUTDOWN_DELAY_S)
    server = app.state.server
    if server is not None:
        server.should_exit = True


@router.get("/desktop", response_model=DesktopResponse)
async def desktop_info(runtime: RuntimeDep) -> DesktopResponse:
    return DesktopResponse(
        version=get_version(), data_dir=str(runtime.config.cold.data_dir), supervised=runtime.config.cold.supervised
    )


@router.post("/restart", status_code=202)
async def restart_server(request: Request, background: BackgroundTasks, config: ConfigDep) -> Response:
    """优雅停机并以退出码 3 退出. 仅监督者在场时可用."""
    if not config.cold.supervised:
        raise HTTPException(status_code=403, detail="未受监督, 不允许重启")
    request.app.state.exit_code = EXIT_RESTART
    background.add_task(_trigger_shutdown, request.app)
    return Response(status_code=202)


@router.get("/release", response_model=ReleaseResponse)
async def get_release(runtime: RuntimeDep) -> ReleaseResponse:
    """查 GitHub latest; 失败时 latest 为空, 不 5xx."""
    current = get_version()
    snapshot = await runtime.release_checker.fetch(
        proxy=runtime.config.hot.network.proxy or None, url=runtime.config.cold.update_url
    )
    latest = snapshot.latest
    return ReleaseResponse(
        current=current,
        latest=latest,
        html_url=snapshot.html_url,
        newer=is_newer(latest, current) if latest is not None else False,
    )
