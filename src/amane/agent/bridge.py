from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..db.embeddings import EmbeddingKind
    from ..db.models import Library
    from ..semantic import SemanticHit


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

    def sync_library(self, lib: Library) -> None: ...


class SemanticSearch(Protocol):
    async def __call__(
        self,
        query: str,
        limit: int,
        kind: EmbeddingKind | None,
        allowed_ids: Sequence[int] | None,
    ) -> list[SemanticHit]: ...


@dataclass
class AgentRuntimeBridge:
    safe_dirs: list[Path] | None = field(default_factory=list)
    watcher: LibraryWatcher | None = None
    cancel_running_task: Callable[[int], Awaitable[bool]] | None = None
    poll_feed: Callable[[int], Awaitable[None]] | None = None
    semantic_search: SemanticSearch | None = None
