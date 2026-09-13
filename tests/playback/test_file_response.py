"""本地文件响应的 Range 契约与断连行为."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from httpx2 import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import Message, Scope

from amane.playback.file_response import IndexedFileResponse

PAYLOAD = bytes(range(64))
HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private"}


def _app(path: Path) -> FastAPI:
    app = FastAPI()

    @app.api_route("/f", methods=["GET", "HEAD"])
    async def serve(request: Request) -> Response:
        return IndexedFileResponse(request, path, media_type="video/mp4", headers=HEADERS)

    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("range_header", "status", "body", "content_range", "detail"),
    [
        (None, 200, PAYLOAD, None, None),
        ("bytes=0-9", 206, PAYLOAD[:10], "bytes 0-9/64", None),
        ("bytes=8-", 206, PAYLOAD[8:], "bytes 8-63/64", None),
        ("bytes=-8", 206, PAYLOAD[-8:], "bytes 56-63/64", None),
        ("bytes=64-", 416, None, "bytes */64", "请求的字节范围无法满足"),
        ("bytes=10-2", 416, None, "bytes */64", "请求的字节范围无法满足"),
    ],
)
async def test_file_response_range_contract(
    tmp_path: Path,
    range_header: str | None,
    status: int,
    body: bytes | None,
    content_range: str | None,
    detail: str | None,
) -> None:
    """单段 Range 返回 206, 不可满足的范围返回 416; 响应头保持码流端点契约.

    416 的正文是 ``detail``: 前端报错时读的是响应体, 只给状态码等于让用户看到「HTTP 416」.
    """
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    headers = {"Range": range_header} if range_header is not None else None
    async with _client(_app(path)) as client:
        response = await client.get("/f", headers=headers)
        assert response.status_code == status
        if body is None:
            assert detail is not None
            assert response.json() == {"detail": detail}
            assert response.headers["content-type"] == "application/json"
        else:
            assert detail is None
            assert response.content == body
            assert response.headers["content-length"] == str(len(body))
            assert response.headers["content-type"] == "video/mp4"
        if content_range is None:
            assert "content-range" not in response.headers
        else:
            assert response.headers["content-range"] == content_range
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers["cache-control"] == "private"
        assert "content-disposition" not in response.headers


@pytest.mark.asyncio
async def test_empty_file_range_is_416_with_detail(tmp_path: Path) -> None:
    """0 字节文件的任何 Range 都不可满足, 416 要说明是空文件而不是越界范围."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"")
    async with _client(_app(path)) as client:
        response = await client.get("/f", headers={"Range": "bytes=0-"})
        assert response.status_code == 416
        assert response.headers["content-range"] == "bytes */0"
        assert response.json() == {"detail": "条目索引的文件为空"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("range_header", "detail"),
    [
        ("bytes=0-1,3-4", "不支持多段 Range"),
        ("bytes=abc", "Range 无法解析"),
        ("items=0-1", "Range 无法解析"),
    ],
)
async def test_file_response_invalid_range_rejected(tmp_path: Path, range_header: str, detail: str) -> None:
    """多段 Range 与语法非法的 Range 归为 400, 与上游代理路径一致."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    async with _client(_app(path)) as client:
        response = await client.get("/f", headers={"Range": range_header})
        assert response.status_code == 400
        assert response.json()["detail"] == detail


@pytest.mark.asyncio
async def test_file_response_head_and_if_range(tmp_path: Path) -> None:
    """HEAD 只发送响应头; ``If-Range`` 不一致时按完整正文输出."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    async with _client(_app(path)) as client:
        head = await client.head("/f")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == "64"
        assert head.headers["accept-ranges"] == "bytes"

        full = await client.get("/f")
        etag = full.headers["etag"]
        matching = await client.get("/f", headers={"Range": "bytes=0-9", "If-Range": etag})
        assert matching.status_code == 206
        assert matching.content == PAYLOAD[:10]

        stale = await client.get("/f", headers={"Range": "bytes=0-9", "If-Range": '"stale"'})
        assert stale.status_code == 200
        assert stale.content == PAYLOAD


@pytest.mark.asyncio
async def test_file_response_missing_file_is_502(tmp_path: Path) -> None:
    """读取期失效返回 502 与可读原因, 而不是 500."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    async with _client(_app(path)) as client:
        path.unlink()
        response = await client.get("/f")
        assert response.status_code == 502
        assert response.json()["detail"] == "条目索引的文件当前无法读取"


class _CountingHandle:
    """计数文件句柄替身: 记录每次读取的字节数, 并在关闭时回调."""

    def __init__(self, payload: bytes, reads: list[int], on_close: Callable[[], None]) -> None:
        self._payload = payload
        self._reads = reads
        self._on_close = on_close
        self._offset = 0

    def seek(self, offset: int) -> int:
        self._offset = offset
        return offset

    def read(self, size: int) -> bytes:
        self._reads.append(size)
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self._on_close()


class _CountingResponse(IndexedFileResponse):
    """用计数句柄替换真实读取, 使「断连后不再读取」可断言."""

    chunk_size = 8

    def __init__(self, request: Request, path: Path, payload: bytes) -> None:
        super().__init__(request, path, media_type="video/mp4", headers=HEADERS)
        self.reads: list[int] = []
        self.closed = False
        self._payload = payload

    async def _open(self) -> Any:
        return _CountingHandle(self._payload, self.reads, self._mark_closed)

    def _mark_closed(self) -> None:
        self.closed = True


def _scope(*, method: str = "GET") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/f",
        "raw_path": b"/f",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }


def _receive(script: list[Message]) -> Callable[[], Awaitable[Message]]:
    """按脚本给出 ASGI 事件; 脚本耗尽后一律报告客户端已断开."""

    async def receive() -> Message:
        return script.pop(0) if script else {"type": "http.disconnect"}

    return receive


def _connected_receive() -> Callable[[], Awaitable[Message]]:
    """给出请求正文后一直挂起 (无人置位), 即客户端没有断开."""

    async def receive() -> Message:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await released.wait()
        return {"type": "http.disconnect"}

    released = asyncio.Event()
    body_sent = False
    return receive


def _body_chunks(messages: list[Message]) -> list[bytes]:
    return [bytes(message["body"]) for message in messages if message["type"] == "http.response.body"]


@pytest.mark.asyncio
async def test_file_response_stops_reading_after_disconnect(tmp_path: Path) -> None:
    """断开消息已经等在通道里时不再输出正文.

    ASGI 服务器在客户端离开后让 ``send`` 静默成功, 读取循环只看 ``send`` 不会停止; 这里让
    ``receive`` 一开始就报告断连, 断言正文一块都不发, 句柄已关闭, 且至多提交了一次已在途的读取.
    """
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    scope = _scope()
    script: list[Message] = [
        {"type": "http.request", "body": b"", "more_body": False},
        {"type": "http.disconnect"},
    ]
    receive = _receive(script)
    response = _CountingResponse(Request(scope, receive), path, PAYLOAD)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await response(scope, receive, send)

    assert [message["type"] for message in sent] == ["http.response.start"]
    assert _body_chunks(sent) == []
    assert len(response.reads) <= 1
    assert response.closed is True


@pytest.mark.asyncio
async def test_file_response_reads_whole_file_without_disconnect(tmp_path: Path) -> None:
    """没有断连时读完整份文件, 并在结束时关闭句柄."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    scope = _scope()
    receive = _connected_receive()
    response = _CountingResponse(Request(scope, receive), path, PAYLOAD)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await response(scope, receive, send)

    assert response.reads == [8] * 8
    assert response.closed is True
    assert b"".join(_body_chunks(sent)) == PAYLOAD
    assert sent[-1]["type"] == "http.response.body"


class _MidStreamDisconnectReceive:
    """正文消息之后挂起, 由首块 ``send`` 触发断开: 断连在流式过程中异步到达."""

    def __init__(self) -> None:
        self._disconnected = asyncio.Event()
        self._body_sent = False

    async def __call__(self) -> Message:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnected.wait()
        return {"type": "http.disconnect"}

    def disconnect(self) -> None:
        self._disconnected.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [True, False])
async def test_file_response_stops_reading_on_midstream_disconnect(tmp_path: Path, wrapped: bool) -> None:
    """断开在流式过程中异步到达时停止读取并关闭句柄, 不再读完整份文件.

    生产栈最外层是 ``BaseHTTPMiddleware``: 它包装出的 ``receive`` 必须真正挂起才会收到
    ``http.disconnect``, 因此 ``Request.is_disconnected`` 那种立刻取消的探测看不到断开. 这里让
    首块发出之后才断开, 断言读循环远早于文件末尾停下 (中间件已缓冲的分块可能仍被送出).
    """
    payload = PAYLOAD * 4
    chunks = len(payload) // 8
    path = tmp_path / "clip.mp4"
    path.write_bytes(payload)
    responses: list[_CountingResponse] = []

    async def dispatch(request: Request, call_next: RequestResponseEndpoint) -> Response:
        return await call_next(request)

    app = FastAPI()
    if wrapped:
        app.add_middleware(BaseHTTPMiddleware, dispatch=dispatch)

    @app.api_route("/f", methods=["GET", "HEAD"])
    async def serve(request: Request) -> Response:
        response = _CountingResponse(request, path, payload)
        responses.append(response)
        return response

    scope = _scope()
    receive = _MidStreamDisconnectReceive()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and len(_body_chunks(sent)) == 1:
            receive.disconnect()
            # 断连消息的唤醒回调先入队, 让等待任务在恢复发送前跑完
            await asyncio.sleep(0)

    await app(scope, receive, send)

    assert len(responses[0].reads) < chunks
    assert len(_body_chunks(sent)) < chunks
    assert responses[0].closed is True
