from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from .rules import MEDIA_EXTENSIONS, compile_skip_patterns, is_in_trash, is_undersized_video

if TYPE_CHECKING:
    from re import Pattern


class LibraryFileKind(StrEnum):
    """单文件分类结果"""

    SKIP = "skip"
    TRASH = "trash"
    MEDIA = "media"


@dataclass(frozen=True, slots=True)
class LibraryHit:
    path: Path
    kind: LibraryFileKind


class LibraryScan:
    """库扫描规则"""

    def __init__(
        self,
        *,
        patterns: list[str] | None = None,
        trailer_pattern: str | None = None,
        blacklist_patterns: Sequence[str] | None = None,
        min_file_size: int = 0,
        media_extensions: frozenset[str] | None = None,
    ) -> None:
        self.patterns = patterns
        self.trailer_pattern = trailer_pattern
        self.blacklist_patterns = list(blacklist_patterns or [])
        self.min_file_size = min_file_size
        self.media_extensions = MEDIA_EXTENSIONS if media_extensions is None else media_extensions
        self._trailer: list[Pattern[str]] | None = compile_skip_patterns([trailer_pattern])
        self._blacklist: list[Pattern[str]] | None = compile_skip_patterns(self.blacklist_patterns)

    def classify(self, path: Path) -> LibraryFileKind | None:
        """判定路径. 回收站与无规则命中的其它文件返回 None."""
        if is_in_trash(path):
            return None
        name = path.name
        if self._blacklist is not None and any(r.search(name) for r in self._blacklist):
            return LibraryFileKind.TRASH
        if self._trailer is not None and any(r.search(name) for r in self._trailer):
            return LibraryFileKind.SKIP
        if is_undersized_video(path, self.min_file_size, media_extensions=self.media_extensions):
            return LibraryFileKind.TRASH
        if self.patterns:
            if any(path.match(p) for p in self.patterns):
                return LibraryFileKind.MEDIA
            return None
        if path.suffix.lower() in self.media_extensions:
            return LibraryFileKind.MEDIA
        return None
