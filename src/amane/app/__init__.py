"""HTTP 层与 CLI/回放共用的进程组合根, 避免依赖 FastAPI 包."""

from .bootstrap import AppSession, build_safe_dirs, start_app
from .runtime import (
    AppRuntime,
    NetworkStack,
    build_handlers,
    build_hot_translator,
    build_network_stack,
    build_r18_db,
)

__all__ = [
    "AppRuntime",
    "AppSession",
    "NetworkStack",
    "build_handlers",
    "build_hot_translator",
    "build_network_stack",
    "build_r18_db",
    "build_safe_dirs",
    "start_app",
]
