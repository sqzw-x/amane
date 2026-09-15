"""LoggingMiddleware 行为: 未捕获异常留痕 + 高频路径降噪."""

import logging
from typing import TYPE_CHECKING

import pytest
import structlog
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from amane.api.middleware import LoggingMiddleware
from amane.observability import setup_logging

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_logging():
    """每个测试前清理 logging 和 structlog 状态 (同 test_logging.py).

    其它用例 (迁移) 会经 ``fileConfig`` 改动全局 logging, 因此这里连 ``disabled`` 一并复位:
    用例之间不允许通过进程级日志状态相互影响.
    """
    root = logging.getLogger("amane")
    root.handlers.clear()
    root.disabled = False
    req = logging.getLogger("amane.request")
    req.handlers.clear()
    req.disabled = False
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    yield
    root.handlers.clear()
    req.handlers.clear()
    structlog.contextvars.clear_contextvars()


class _CaptureHandler(logging.Handler):
    """捕获 amane.request logger 的原始 LogRecord (未经 formatter)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _Direct401Middleware(BaseHTTPMiddleware):
    """类比 TokenAuth: 不发内层, 直接返回 401."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        return JSONResponse(status_code=401, content={"detail": "nope"})


class _RaisingMiddleware(BaseHTTPMiddleware):
    """类比 TokenAuth 自身 bug: 未调用内层就抛未知异常."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raise RuntimeError("inner middleware boom")


def _make_app(*, inner: type[BaseHTTPMiddleware] | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/api/system/desktop")
    async def desktop() -> dict[str, str]:
        return {"version": "x"}

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/api/guard")
    async def guard() -> None:
        from fastapi import HTTPException

        raise HTTPException(403, "nope")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    if inner is not None:
        app.add_middleware(inner)
    app.add_middleware(LoggingMiddleware)
    return app


def _client(app: FastAPI) -> AsyncClient:
    # raise_app_exceptions=False: 让 Starlette 500 响应可见, 而非在客户端重抛
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


def _capture(handler: _CaptureHandler) -> list[dict]:
    return [r.msg for r in handler.records if isinstance(r.msg, dict)]


class TestFailedRequestLogging:
    """一次请求恰好一条记录; 异常与内层中间件短路都不允许漏记."""

    @pytest.mark.parametrize(
        ("path", "inner", "status", "event", "exception_contains"),
        [
            ("/boom", None, 500, "request failed", "RuntimeError: boom"),
            ("/api/guard", None, 403, "request completed", None),
            ("/api/health", _Direct401Middleware, 401, "request completed", None),
            ("/api/health", _RaisingMiddleware, 500, "request failed", "RuntimeError: inner middleware boom"),
        ],
        ids=["route-exception", "http-exception", "inner-direct-401", "inner-exception"],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_request_logged_exactly_once(
        self,
        tmp_path: Path,
        path: str,
        inner: type[BaseHTTPMiddleware] | None,
        status: int,
        event: str,
        exception_contains: str | None,
    ):
        setup_logging(level="INFO", log_dir=tmp_path)
        handler = _CaptureHandler()
        logging.getLogger("amane.request").addHandler(handler)
        try:
            async with _client(_make_app(inner=inner)) as client:
                resp = await client.get(path)
            assert resp.status_code == status
        finally:
            logging.getLogger("amane.request").removeHandler(handler)

        records = _capture(handler)
        assert len(records) == 1
        payload = records[0]
        assert payload["event"] == event
        assert payload["status"] == status
        assert payload["path"] == path
        assert payload["method"] == "GET"
        if exception_contains is None:
            assert "exception" not in payload
        else:
            assert exception_contains in payload["exception"]


class TestNoisyPaths:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_desktop_poll_absent_at_info_level(self, tmp_path: Path):
        """默认 INFO: /api/system/desktop 高频轮询不落 request.log, 普通路径仍记录."""
        setup_logging(level="INFO", log_dir=tmp_path)
        handler = _CaptureHandler()
        logging.getLogger("amane.request").addHandler(handler)
        try:
            async with _client(_make_app()) as client:
                assert (await client.get("/api/system/desktop")).status_code == 200
                assert (await client.get("/api/health")).status_code == 200
        finally:
            logging.getLogger("amane.request").removeHandler(handler)

        records = _capture(handler)
        assert any(
            m.get("event") == "request completed" and m.get("path") == "/api/health" and m.get("status") == 200
            for m in records
        )
        assert not any(m.get("path") == "/api/system/desktop" for m in records)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_desktop_poll_visible_only_at_debug(self, tmp_path: Path):
        """DEBUG 级别下降噪路径可恢复可见 (log = debug 而非丢弃)."""
        setup_logging(level="DEBUG", log_dir=tmp_path)
        handler = _CaptureHandler()
        logging.getLogger("amane.request").addHandler(handler)
        try:
            async with _client(_make_app()) as client:
                assert (await client.get("/api/system/desktop")).status_code == 200
        finally:
            logging.getLogger("amane.request").removeHandler(handler)

        record = handler.records[0]
        assert record.levelno == logging.DEBUG
        payload = record.msg
        assert isinstance(payload, dict)
        assert payload["path"] == "/api/system/desktop"
