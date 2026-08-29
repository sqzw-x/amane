"""写面工具所需的运行时桥接 (路径边界 / 文件监控 / Feed / 任务取消)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class LibraryWatcher(Protocol):
    def add_library(
        self,
        path: str,
        library_id: int,
        recursive: bool = True,
        patterns: list[str] | None = None,
        skip_patterns: Sequence[str | None] | None = None,
        min_file_size: int = 0,
    ) -> None: ...

    def remove_library(self, library_id: int) -> None: ...


@dataclass
class AgentRuntimeBridge:
    """由 AppRuntime 装配后注入 AgentService / AgentDeps."""

    safe_dirs: list[Path] | None = field(default_factory=list)
    watcher: LibraryWatcher | None = None
    cancel_running_task: Callable[[int], Awaitable[bool]] | None = None
    poll_feed: Callable[[int], Awaitable[None]] | None = None
