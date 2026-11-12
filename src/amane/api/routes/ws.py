import hmac
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..middleware import API_TOKEN_COOKIE, browser_source_allowed

if TYPE_CHECKING:
    from ...app.runtime import AppRuntime

router = APIRouter()


def _get_runtime(ws: WebSocket) -> AppRuntime:
    """WebSocket 不支持 Depends 注入."""
    return ws.app.state.runtime


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """单向: 服务端 broadcast, 客户端只读. 保活由协议层 PING/PONG, 无需应用层心跳."""
    runtime = _get_runtime(ws)
    # 浏览器 WebSocket 无法自定义 header; 同源握手携带 API_TOKEN_COOKIE.
    # None = 关闭校验. token 不出现在 URL / 访问日志.
    token = runtime.api_token
    if token is not None:
        if not hmac.compare_digest(ws.cookies.get(API_TOKEN_COOKIE, ""), token):
            await ws.close(code=1008)  # policy violation
            return
        # 握手同样按来源检查, 跨站页面不得借 cookie 接入
        if not browser_source_allowed(ws.headers, runtime.config.cold.trusted_origins):
            await ws.close(code=1008)
            return
    event_bus = runtime.event_bus
    await event_bus.connect(ws)
    try:
        while True:
            # 客户端不发送消息; 仅通过 receive 阻塞以感知断开
            await ws.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(ws)
