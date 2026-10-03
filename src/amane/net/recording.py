"""HTTP 录制接线 - ContextVar 与可选 recorder lookup.

WebClient 不依赖 observability; 由 observability.recorder 在导入时
``bind_http_recorder_lookup(get_recorder)`` 注入查找函数.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Protocol


class HttpExchangeRecorder(Protocol):
    def record_http(
        self,
        *,
        method: str,
        url: str,
        status: int | None,
        error: str | None,
        content_type: str | None,
        body: bytes | None,
        elapsed_ms: int | None,
        attempts: int | None = None,
        capture_body: bool = True,
    ) -> None: ...


_skip_http_body_ctx: ContextVar[bool] = ContextVar("skip_http_body", default=False)
_recorder_lookup: Callable[[], HttpExchangeRecorder | None] | None = None


def set_skip_http_body(skip: bool) -> Token[bool]:
    return _skip_http_body_ctx.set(skip)


def reset_skip_http_body(token: Token[bool]) -> None:
    _skip_http_body_ctx.reset(token)


def skip_http_body() -> bool:
    return _skip_http_body_ctx.get()


@contextmanager
def skip_body_recording() -> Iterator[None]:
    """请求期间跳过 body 落盘, 只记 meta (get_bytes / download / solver 响应)."""
    token = set_skip_http_body(True)
    try:
        yield
    finally:
        reset_skip_http_body(token)


def bind_http_recorder_lookup(lookup: Callable[[], HttpExchangeRecorder | None]) -> None:
    """通常为 observability.get_recorder."""
    global _recorder_lookup
    _recorder_lookup = lookup


def get_bound_http_recorder() -> HttpExchangeRecorder | None:
    if _recorder_lookup is None:
        return None
    return _recorder_lookup()
