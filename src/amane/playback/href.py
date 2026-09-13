"""Canonical browser-facing playback paths under ``/api/playback``.

一条流一个地址: ``key`` 是插件声明的流标识. 没有 key 的形式也保留 —— 它表示「由插件自己挑一条」,
不可用行没有 key 可写, 其 ``href`` 只能落到这里.
"""

from __future__ import annotations

API_PLAYBACK = "/api/playback"


def stream_href(source_id: str, metadata_id: int, key: str | None) -> str:
    base = f"{API_PLAYBACK}/{source_id}/{metadata_id}"
    if key is None:
        return base
    return f"{base}/streams/{key}"


def playlist_href(source_id: str, metadata_id: int, key: str | None) -> str:
    return f"{stream_href(source_id, metadata_id, key)}/index.m3u8"


def hls_part_href(source_id: str, metadata_id: int, key: str | None, token: str) -> str:
    return f"{stream_href(source_id, metadata_id, key)}/hls/{token}"


def subtitle_href(source_id: str, metadata_id: int, key: str | None, track_id: str) -> str:
    return f"{stream_href(source_id, metadata_id, key)}/subtitles/{track_id}"
