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
    """归类结果: 媒体入库; 无效文件进清理清单; 跳过既不入库也不进清单."""

    SKIP = "skip"
    UNWANTED = "unwanted"
    MEDIA = "media"


class UnwantedKind(StrEnum):
    """无效文件的命中规则. 预告片是预期文件, 不算无效."""

    BLACKLIST = "blacklist"
    UNDERSIZED = "undersized"


@dataclass(frozen=True, slots=True)
class LibraryHit:
    path: Path
    kind: LibraryFileKind


class LibraryScan:
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

    def unwanted_kind(self, path: Path) -> UnwantedKind | None:
        """无效文件的命中规则; 未命中返回 None.

        文件黑名单先于预告片与大小判定; 预告片不算无效, 否则低码率预告片会被判成小于最小视频大小.
        stat 失败 (含悬空链接) 不判大小, 见 `is_undersized_video`.
        """
        if is_in_trash(path):
            return None
        name = path.name
        if self._matches(self._blacklist, name):
            return UnwantedKind.BLACKLIST
        if self._matches(self._trailer, name):
            return None
        if is_undersized_video(path, self.min_file_size, media_extensions=self.media_extensions):
            return UnwantedKind.UNDERSIZED
        return None

    def classify(self, path: Path) -> LibraryFileKind | None:
        """回收目录与无规则命中的其它文件返回 None."""
        if is_in_trash(path):
            return None
        if self.unwanted_kind(path) is not None:
            return LibraryFileKind.UNWANTED
        # 预告片 → 跳过.
        if self._matches(self._trailer, path.name):
            return LibraryFileKind.SKIP
        # glob 或扩展名命中 → 媒体; 其余不产出.
        if self.patterns:
            if any(path.match(p) for p in self.patterns):
                return LibraryFileKind.MEDIA
            return None
        if path.suffix.lower() in self.media_extensions:
            return LibraryFileKind.MEDIA
        return None

    @staticmethod
    def _matches(patterns: list[Pattern[str]] | None, name: str) -> bool:
        return patterns is not None and any(r.search(name) for r in patterns)
