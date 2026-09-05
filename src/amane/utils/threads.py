"""同步函数进入默认线程池, 供事件循环 ``await``. 已在工作线程里用 ``.sync``, 不要再进一次线程池."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import update_wrapper
from pathlib import Path
from typing import Any

from .path import existing_disk_path as _existing_disk_path


class in_thread[**P, R]:
    """``await fn(...)`` 进入线程池, ``fn.sync(...)`` 原地执行."""

    __slots__ = ("__dict__", "__wrapped__", "sync")

    def __init__(self, fn: Callable[P, R]) -> None:
        self.sync = fn
        update_wrapper(self, fn)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, R]:
        return asyncio.to_thread(self.sync, *args, **kwargs)


@in_thread
def existing_disk_path(path: Path, *, follow_symlinks: bool = True) -> Path | None:
    """先试传入路径, 不存在再试规范等价的 NFC / NFD."""
    return _existing_disk_path(path, follow_symlinks=follow_symlinks)


@in_thread
def path_exists(path: Path, *, follow_symlinks: bool = True) -> bool:
    return _existing_disk_path(path, follow_symlinks=follow_symlinks) is not None


@in_thread
def path_is_dir(path: Path) -> bool:
    return path.is_dir()
