from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from ..app.bootstrap import start_app
from ..version import get_version
from .middleware import LoggingMiddleware, TokenAuthMiddleware
from .routes import router
from .spa import mount_spa

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi.routing import APIRoute

GZIP_MINIMUM_SIZE = 500
"""压缩阈值, 只在正文以单条完整消息交出时才生效; 本栈内层是 BaseHTTPMiddleware, 实际不生效."""

GZIP_COMPRESSLEVEL = 6
"""压缩级别. Starlette 默认 9, 但实测 (首屏 JS) 只比 6 小 0.2%, 耗时却多 15%."""

GZIP_THREAD_MINIMUM_SIZE = 128 * 1024
"""超过此长度的正文交给工作线程压缩, 避免大响应把事件循环钉住."""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """进程编排在 ``amane.app.bootstrap``; 此处把 ``AppSession.runtime`` 写入 ``app.state``, 退出时 ``aclose``."""
    session = await start_app()
    app.state.runtime = session.runtime
    try:
        yield
    finally:
        await session.aclose()


def generate_operation_id(route: APIRoute) -> str:
    """使用路由函数名, 避免 FastAPI 默认的冗长 operation ID."""
    return route.name


def create_app() -> FastAPI:
    app = FastAPI(
        title="Amane API",
        version=get_version(),
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        generate_unique_id_function=generate_operation_id,
    )
    app.state.exit_code = 0
    app.state.server = None
    app.include_router(router)
    mount_spa(app)  # 必须在 API 路由之后, 以免 catch-all 覆盖 API
    app.add_middleware(TokenAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 最后注册 LoggingMiddleware, 使其位于最外层, 才能记录内层自定义中间件的异常
    app.add_middleware(LoggingMiddleware)
    # 压缩放在最外层: 内层的 BaseHTTPMiddleware 会把正文改成分块消息, 压缩只能"边流边压"
    # (普通 JSON 也会被压且丢掉 content-length). 真正受益的是几百 KiB 的 SPA 产物与大 JSON;
    # 图片 / 视频 / SSE 由 GZipMiddleware 自带排除表挡住.
    app.add_middleware(
        GZipMiddleware,
        minimum_size=GZIP_MINIMUM_SIZE,
        compresslevel=GZIP_COMPRESSLEVEL,
        thread_minimum_size=GZIP_THREAD_MINIMUM_SIZE,
    )
    return app
