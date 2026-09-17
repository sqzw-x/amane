import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from starlette.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger()


def _project_root() -> Path:
    """定位仓库根 (含 pyproject.toml 与 web/), 兼容 src 布局与冻结包."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "web").is_dir():
            return parent
    return here.parents[3]


def _default_dist() -> Path:
    override = os.environ.get("AMANE_WEB_DIST")
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "web" / "dist"


# 不应被 SPA 拦截的路径前缀
_RESERVED_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json", "/assets")

# 入口文档与 dist 里未改名的文件必须每次校验: 文件名不带 hash, 直接用缓存会让壳停在旧的那份.
# 加 `public` 是与 `support/http_cache.py` 的写法一致 (文档不含用户数据, 共享缓存也可以存).
_NO_CACHE = "public, no-cache"
# `/assets` 下的文件名带内容 hash: 内容变了名字就变, 因此可以长期缓存 (RFC 8246 的 `immutable`).
_IMMUTABLE = "public, max-age=31536000, immutable"


class _DistFiles(StaticFiles):
    """按 dist 目录直出的静态文件, 统一补一条 `Cache-Control`.

    走 `StaticFiles` 而不是 `FileResponse` 是为了拿到条件请求与 Range: `FileResponse` 自己不看
    `If-None-Match` / `If-Modified-Since`, 用它会每个响应都整份重传 — 打了 `no-cache` 之后浏览器每次加载
    都会发条件请求, 那种情况下全量重传正是要避免的.

    调用方必须保证 `directory` 下只放对应缓存策略适用的东西: 本仓库里 `assets/` 只放 Vite 的带 hash 产物,
    `web/public/` 下的未改名文件落在 dist 根目录, 因此走的是 `_NO_CACHE`.
    """

    def __init__(self, *, directory: str, cache_control: str) -> None:
        super().__init__(directory=directory)
        self._cache_control = cache_control

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = self._cache_control
        return response


class _SPAFallbackMiddleware:
    """未匹配 API 的 GET / HEAD 回退到 index.html. 含 ``..`` 的路径返回 400."""

    def __init__(self, app: ASGIApp, dist_dir: Path) -> None:
        self._app = app
        self._dist = dist_dir
        self._files = _DistFiles(directory=str(dist_dir), cache_control=_NO_CACHE)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "/")

        # 保留路径直接透传
        if any(path.startswith(p) for p in _RESERVED_PREFIXES):
            await self._app(scope, receive, send)
            return

        # 禁止路径遍历
        if ".." in path:
            response: Response = HTMLResponse(status_code=400, content="Bad request")
            await response(scope, receive, send)
            return

        # HEAD 与 GET 一样处理: 两者除响应体外的头部必须一致 (RFC 9110 §9.3.2), 其余方法交给上游.
        if scope.get("method", "GET") not in ("GET", "HEAD"):
            await self._app(scope, receive, send)
            return

        # dist 内有这个文件就发它, 否则回退到 index.html.
        clean_path = path.lstrip("/")
        if clean_path and (self._dist / clean_path).is_file():
            response = await self._files.get_response(clean_path, scope)
        else:
            response = await self._files.get_response("index.html", scope)
        await response(scope, receive, send)


def mount_spa(app: FastAPI, dist_dir: Path | None = None) -> None:
    """dist 不存在则跳过 (开发时由 Vite 提供)."""
    dist = dist_dir or _default_dist()

    if not dist.exists():
        logger.info("spa dist not found, skipping mount", path=str(dist))
        return

    index_html = dist / "index.html"
    if not index_html.exists():
        logger.warning("index.html not found, spa not mounted", path=str(dist))
        return

    # 挂载 /assets (Vite 产物文件名带 hash, 可长期缓存)
    assets_dir = dist / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            _DistFiles(directory=str(assets_dir), cache_control=_IMMUTABLE),
            name="spa-assets",
        )

    # 在路由匹配之前拦截非 API 请求, 回退到 index.html
    app.add_middleware(_SPAFallbackMiddleware, dist_dir=dist)  # type: ignore[arg-type]

    logger.info("spa mounted", path=str(dist))
