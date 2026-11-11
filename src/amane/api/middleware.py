import hmac
import time
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.datastructures import Headers
    from starlette.requests import Request
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger("amane.request")

#: token 校验豁免的 API 路径: 就绪检查与接入引导. 免鉴权在此显式登记, 便于集中审查.
_TOKEN_EXEMPT = {"/api/health", "/api/agent-guide"}

#: 图片 token 只对 ``/api/resources/*`` 的 GET 请求生效, 供无法附加请求头的消费方
#: (Jellyfin / Emby 宿主) 获取图片.
_IMAGE_TOKEN_PREFIX = "/api/resources/"
_IMAGE_TOKEN_QUERY = "token"

#: 高频轮询/静态资源降噪: 不写入 info 级, 仅在 DEBUG 可见
#: (/api/ws 长连接受 EventBus 事件驱动; /api/system/desktop 是菜单栏 bar 每 3s 轮询, 见 desktop.md)
_NOISY_PATHS = frozenset({"/api/ws", "/favicon.ico", "/api/system/desktop"})

#: HttpOnly cookie 名: 浏览器子资源 (`<img>` 加载 /api/resources/* 等)
#: 与 WebSocket 握手无法带 Authorization header, 首次 Bearer 认证成功后由中间件
#: 下发同值 cookie 供其使用. SameSite=Lax + host-only 只挡跨站嵌入 / 跨站 fetch;
#: 同站其它端口的页面浏览器仍会附带 cookie, 由写方法的来源检查 (见下) 拦截.
#: API token 本身不出现在 URL / 访问日志; 图片 token 是唯一经由 URL 传递的凭证.
API_TOKEN_COOKIE = "amane_token"
_COOKIE_MAX_AGE = 30 * 24 * 3600

#: 只读方法不改变状态, 不做来源检查 (浏览器子资源加载也走 cookie).
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: ``Sec-Fetch-Site`` 中视为可信的取值: 同源, 以及用户直接发起 (地址栏 / 书签).
_TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})

#: 未配置 ``AMANE_TRUSTED_ORIGINS`` 时信任的开发来源: Vite 代理改写 Host,
#: 浏览器 Origin 仍是 :5173, 而后端看到的是 :8000.
_DEV_TRUSTED_ORIGINS = frozenset({"http://localhost:5173", "http://127.0.0.1:5173"})

_UNTRUSTED_WRITE_DETAIL = "写请求来源不受信任, 已拒绝."


def _normalized_origin(raw: str) -> str | None:
    """归一为 ``scheme://host[:port]``; 非 http(s) 或无法解析返回 None (``Origin: null`` 属于此类)."""
    try:
        parsed = urlsplit(raw.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _origin_allowed(origin: str, host: str | None, configured: Sequence[str]) -> bool:
    """``Origin`` 与请求 ``Host`` 同 host:port, 或命中白名单.

    只比 host:port 不比 scheme: TLS 反向代理终止后 request scheme 与浏览器 Origin 不同, cookie 也不按 scheme 隔离.
    """
    normalized = _normalized_origin(origin)
    if normalized is None:
        return False
    if host and urlsplit(normalized).netloc == host.strip().lower():
        return True
    trusted = (
        frozenset(normalized_raw for raw in configured if (normalized_raw := _normalized_origin(raw)) is not None)
        if configured
        else _DEV_TRUSTED_ORIGINS
    )
    return normalized in trusted


def browser_source_allowed(headers: Headers, configured_origins: Sequence[str]) -> bool:
    """cookie 认证的写方法是否来自可信来源.

    浏览器自带的 ``Sec-Fetch-Site`` 优先: 同站 (另一端口) 与跨站一律拒绝. 缺失时退回 ``Origin``:
    无 ``Origin`` 的是脚本 / API 客户端, 放行; 否则须与 ``Host`` 同源或命中白名单.
    没配置白名单时信任开发默认, 见 ``_DEV_TRUSTED_ORIGINS``.
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.strip().lower() in _TRUSTED_FETCH_SITES
    origin = headers.get("origin")
    if origin is None:
        return True
    return _origin_allowed(origin, headers.get("host"), configured_origins)


def _token_matches(presented: str | None, expected: str) -> bool:
    """常量时间比较. ``compare_digest`` 对非 ASCII 的 ``str`` 抛出 ``TypeError``, 故先按 UTF-8 编码."""
    return presented is not None and hmac.compare_digest(presented.encode(), expected.encode())


class CORSPolicyMiddleware:
    """按 token 状态决定是否放开跨源.

    token 开启时沿用 ``allow_origins=["*"]`` (无凭据, 跨源也读不到 cookie). token 关闭时任何人可直接读写 API,
    跨源放开等于让任意页面操作本机服务, 因此只允许配置的 ``cors_origins``; 默认空 = 不返回 CORS 头, 预检不通过,
    同源页面照常可用. ``runtime.api_token`` 只在请求期存在, 故不能在 ``create_app`` 里构造静态 CORS.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._open = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._restricted: CORSMiddleware | None = None
        self._restricted_origins: tuple[str, ...] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        runtime = scope["app"].state.runtime
        if runtime.api_token is not None:
            await self._open(scope, receive, send)
            return

        origins = tuple(runtime.config.cold.cors_origins)
        restricted = self._restricted
        if restricted is None or origins != self._restricted_origins:
            restricted = CORSMiddleware(
                self._app,
                allow_origins=list(origins),
                allow_credentials=False,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            self._restricted = restricted
            self._restricted_origins = origins
        await restricted(scope, receive, send)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """除豁免路径外的 ``/api/*`` 须 Bearer 或同值 HttpOnly cookie.
    资源端点另接受图片 token (query ``token`` 或 Bearer), 该凭证只能读取资源,
    代理端点只直出已缓存资源. ``runtime.api_token is None`` = 关闭校验.
    非 /api (SPA/静态) 不校验, 登录页须可加载.
    Starlette BaseHTTPMiddleware 不拦截 ws scope, WebSocket 校验在端点内完成.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # 资源端点据此区分凭证: 图片 token 的代理请求只从缓存读取资源
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
            # cookie 是浏览器凭证, 非安全方法须来自同源页面
            if request.method not in _SAFE_METHODS and not browser_source_allowed(
                request.headers, request.app.state.runtime.config.cold.trusted_origins
            ):
                return JSONResponse(status_code=403, content={"detail": _UNTRUSTED_WRITE_DETAIL})
            return await call_next(request)

        image_token = request.app.state.runtime.image_token
        if (
            image_token is not None
            and path.startswith(_IMAGE_TOKEN_PREFIX)
            and request.method == "GET"
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
