"""Canonical browser-facing playback paths under ``/api/playback``."""

from __future__ import annotations

API_PLAYBACK = "/api/playback"


def stream_href(source_id: str, metadata_id: int, media_file_id: int | None) -> str:
    base = f"{API_PLAYBACK}/{source_id}/{metadata_id}"
    if media_file_id is None:
        return base
    return f"{base}/files/{media_file_id}"


def playlist_href(source_id: str, metadata_id: int, media_file_id: int | None) -> str:
    return f"{stream_href(source_id, metadata_id, media_file_id)}/index.m3u8"


def hls_part_href(source_id: str, metadata_id: int, media_file_id: int | None, token: str) -> str:
    return f"{stream_href(source_id, metadata_id, media_file_id)}/hls/{token}"


def subtitle_href(source_id: str, metadata_id: int, media_file_id: int | None, track_id: str) -> str:
    return f"{stream_href(source_id, metadata_id, media_file_id)}/subtitles/{track_id}"
