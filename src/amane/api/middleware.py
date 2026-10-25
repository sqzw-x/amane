import hmac
import time
import uuid
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.requests import Request

logger = structlog.get_logger("amane.request")

#: token 校验豁免的 API 路径: 就绪检查与接入引导. 免鉴权在此显式登记, 便于集中审查.
_TOKEN_EXEMPT = {"/api/health", "/api/agent-guide"}

#: 图片 token 只在这些端点与方法上生效: 资源直出与代理. 供无法附加请求头的消费方
#: (Jellyfin / Emby 宿主) 取图; query ``token`` 与 ``Authorization: Bearer`` 均可传递.
_IMAGE_TOKEN_PREFIX = "/api/resources/"
_IMAGE_TOKEN_METHODS = frozenset({"GET", "HEAD"})
_IMAGE_TOKEN_QUERY = "token"

#: 高频轮询/静态资源降噪: 不写入 info 级, 仅在 DEBUG 可见
#: (/api/ws 长连接受 EventBus 事件驱动; /api/system/desktop 是菜单栏 bar 每 3s 轮询, 见 desktop.md)
_NOISY_PATHS = frozenset({"/api/ws", "/favicon.ico", "/api/system/desktop"})

#: HttpOnly cookie 名: 浏览器子资源 (`<img>` 加载 /api/resources/* 等)
#: 与 WebSocket 握手无法带 Authorization header, 首次 Bearer 认证成功后由中间件
#: 下发同值 cookie 供其使用. SameSite=Lax + host-only 使跨站嵌入 / 跨站 fetch 不
#: 带 cookie — rebinding 防线不弱化. API token 本身不出现在 URL / 访问日志;
#: 图片 token 是唯一出现在 URL 中的凭证, 只覆盖资源端点.
API_TOKEN_COOKIE = "amane_token"
_COOKIE_MAX_AGE = 30 * 24 * 3600


def _token_matches(presented: str | None, expected: str) -> bool:
    """常量时间比较; 非 ASCII 输入编码后比较, ``compare_digest`` 不接受非 ASCII str."""
    return presented is not None and hmac.compare_digest(presented.encode(), expected.encode())


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """除豁免路径外的 ``/api/*`` 须 Bearer 或同值 HttpOnly cookie.
    资源端点另接受图片 token (query ``token`` 或 Bearer), 该凭证只能读取资源,
    代理端点只直出已缓存资源. ``runtime.api_token is None`` = 关闭校验.
    非 /api (SPA/静态) 不校验, 登录页须可加载.
    Starlette BaseHTTPMiddleware 不拦截 ws scope, WebSocket 校验在端点内完成.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # 资源端点据此区分凭证: 图片 token 的代理请求只读缓存
        request.state.via_image_token = False

        # WS 握手是 HTTP 请求, 但浏览器无法自定义 header, token 经由 cookie;
        # 校验在 ws 端点内完成 — 此处直接透传.
        if request.scope["type"] != "http":
            return await call_next(request)

        token = request.app.state.runtime.api_token
        if token is None:
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/") or path in _TOKEN_EXEMPT:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if _token_matches(auth, f"Bearer {token}"):
            response = await call_next(request)
            # header 认证成功 → 顺带刷新/下发 cookie (子资源/WS 无法带 header)
            if not _token_matches(request.cookies.get(API_TOKEN_COOKIE), token):
                response.set_cookie(
                    API_TOKEN_COOKIE,
                    token,
                    max_age=_COOKIE_MAX_AGE,
                    path="/api",
                    httponly=True,
                    samesite="lax",
                    secure=request.url.scheme == "https",
                )
            return response
        if _token_matches(request.cookies.get(API_TOKEN_COOKIE), token):
            return await call_next(request)

        image_token = request.app.state.runtime.image_token
        if (
            image_token is not None
            and path.startswith(_IMAGE_TOKEN_PREFIX)
            and request.method in _IMAGE_TOKEN_METHODS
            and (
                _token_matches(request.query_params.get(_IMAGE_TOKEN_QUERY), image_token)
                or _token_matches(auth, f"Bearer {image_token}")
            )
        ):
            request.state.via_image_token = True
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "缺少或无效的 API token."})


class LoggingMiddleware(BaseHTTPMiddleware):
    """端点未捕获异常须在 Starlette 返回 500 之前写入 ``request failed`` (含 traceback),
    否则 request.log 对该请求完全无记录.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        request_id = uuid.uuid4().hex[:12]

        # 绑定请求上下文, 下游日志自动携带
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.monotonic()
        path = request.url.path

        try:
            response = await call_next(request)
        except Exception:
            # Starlette 返回 500 之前必须留痕, 否则 request.log 对该请求完全无记录
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "request failed",
                method=request.method,
                path=path,
                status=500,
                duration_ms=round(duration_ms, 1),
                exc_info=True,
            )
            raise

        duration_ms = (time.monotonic() - start) * 1000

        # 高频轮询/静态资源不写入 info 级
        log = logger.debug if path in _NOISY_PATHS else logger.info
        log(
            "request completed",
            method=request.method,
            path=path,
            status=response.status_code,
            duration_ms=round(duration_ms, 1),
        )

        # 写入响应头便于排查
        response.headers["X-Request-ID"] = request_id
        return response
