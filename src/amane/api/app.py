from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..app.bootstrap import start_app
from ..version import get_version
from .middleware import LoggingMiddleware, TokenAuthMiddleware
from .routes import public_router, router
from .spa import mount_spa

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi.routing import APIRoute


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
    app.include_router(public_router)  # 免鉴权, 不属于 /api
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
    return app
