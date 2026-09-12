"""Builtin ``local`` playback source: Range output of indexed MediaFile paths."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ..plugins.api import (
    FilePlaybackTarget,
    PlaybackMediaFile,
    PlaybackOffer,
    PlaybackProvider,
    PlaybackQuery,
)
from ..utils.path import existing_disk_path, is_any_descendant, nfc_path
from ..utils.threads import in_thread

LOCAL_SOURCE_ID = "local"
_STRM_SUFFIX = ".strm"


def media_type_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is not None and guessed.startswith(("video/", "audio/")):
        return guessed
    return "video/mp4"


def _playable_files(query: PlaybackQuery) -> list[PlaybackMediaFile]:
    selected = query.selected_file_id
    files: list[PlaybackMediaFile] = []
    for item in query.files:
        if selected is not None and item.id != selected:
            continue
        if Path(item.path).suffix.casefold() == _STRM_SUFFIX:
            continue
        files.append(item)
    return files


def _pick_default(files: list[PlaybackMediaFile]) -> PlaybackMediaFile:
    return max(files, key=lambda item: (item.size or 0, item.id))


@in_thread
def _resolve_local_file(
    item: PlaybackMediaFile,
    safe_dirs: list[Path] | None,
) -> tuple[Path, int] | None:
    on_disk = existing_disk_path(Path(item.path))
    if on_disk is None or not on_disk.is_file():
        return None
    resolved = on_disk.resolve()
    if nfc_path(str(resolved)) != nfc_path(item.path):
        return None
    if safe_dirs is not None:
        if not safe_dirs:
            return None
        if not is_any_descendant(resolved, *safe_dirs):
            return None
    size = resolved.stat().st_size
    return resolved, size


class LocalPlaybackProvider(PlaybackProvider):
    def __init__(self, safe_dirs: list[Path] | None) -> None:
        self._safe_dirs = safe_dirs

    async def probe(self, query: PlaybackQuery) -> PlaybackOffer | None:
        chosen = await self._choose(query)
        if chosen is None:
            return None
        media_file, path, _size = chosen
        return PlaybackOffer(
            name="本地文件",
            content_type=media_type_for_path(path),
            seekable=True,
            media_file_id=media_file.id,
        )

    async def resolve(self, query: PlaybackQuery) -> FilePlaybackTarget | None:
        chosen = await self._choose(query)
        if chosen is None:
            return None
        media_file, path, size = chosen
        return FilePlaybackTarget(
            path=path,
            content_type=media_type_for_path(path),
            size=size,
            media_file_id=media_file.id,
        )

    async def _choose(self, query: PlaybackQuery) -> tuple[PlaybackMediaFile, Path, int] | None:
        files = _playable_files(query)
        if not files:
            return None
        target = files[0] if query.selected_file_id is not None else _pick_default(files)
        resolved = await _resolve_local_file(target, self._safe_dirs)
        if resolved is None:
            return None
        path, size = resolved
        return target, path, size
