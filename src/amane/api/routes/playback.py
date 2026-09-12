from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from ...net.errors import SourceError
from ...playback.factory import SOURCE_ID_MAX_LEN, PlaybackFactory
from ...playback.file_response import IndexedFileResponse
from ...playback.hls import PLAYLIST_CACHE_CONTROL, is_hls_content_type
from ...playback.href import playlist_href, stream_href, subtitle_href
from ...playback.proxy import NOSNIFF
from ...playback.query import playback_query
from ...plugins.api import FilePlaybackTarget, PlaybackQuery, UpstreamPlaybackTarget
from ..deps import RepoDep, RuntimeDep
from ..models.playback import PlaybackSourceItem, PlaybackSourceListResponse, PlaybackSubtitleItem

router = APIRouter(prefix="/playback", tags=["playback"])

_TOKEN_PATTERN = r"^[0-9a-f]{32}$"
#: 进路径的标识 (流的 key, 字幕轨道 id) 与 ``PlaybackOffer.key`` 同一形状: 首字符是字母或数字,
#: 于是 ``.`` 与 ``..`` 这类会被归一化的段不可能出现.
_PATH_SEGMENT_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$"
_TRACK_PATTERN = _PATH_SEGMENT_PATTERN
_KEY_PATTERN = _PATH_SEGMENT_PATTERN


def _source_href(source_id: str, metadata_id: int, key: str | None, content_type: str) -> str:
    if is_hls_content_type(content_type):
        return playlist_href(source_id, metadata_id, key)
    return stream_href(source_id, metadata_id, key)


def _etag_for(items: list[PlaybackSourceItem]) -> str:
    payload = json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _etag_matches(header: str | None, etag: str) -> bool:
    """按 ``If-None-Match`` 语义比较: 逗号分隔的多值, 弱校验前缀 ``W/``, 以及 ``*``."""
    if header is None:
        return False
    candidates = [part.strip() for part in header.split(",")]
    return any(candidate == "*" or candidate.removeprefix("W/") == etag for candidate in candidates)


async def _load_query(
    repo: RepoDep,
    metadata_id: int,
    selected_key: str | None,
) -> PlaybackQuery:
    """装配查询快照.

    ``selected_key`` 逐字交给插件: 主机不解释它, 也不核对它对应哪个文件 (那是插件的标识), 认不出来
    的 key 由插件以自身原因拒绝.
    """
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="元数据不存在")
    files = await repo.get_media_by_metadata_id(metadata_id)
    library_paths: dict[int, str] = {}
    for item in files:
        if item.library_id in library_paths:
            continue
        library = await repo.get_library(item.library_id)
        if library is not None:
            library_paths[item.library_id] = library.path
    return playback_query(metadata, files, selected_key=selected_key, library_paths=library_paths)


def _playback_http_error(exc: SourceError) -> HTTPException:
    return HTTPException(status_code=502, detail=exc.detail or "上游失败")


@router.get("/sources", response_model=PlaybackSourceListResponse)
async def list_playback_sources(
    request: Request,
    repo: RepoDep,
    runtime: RuntimeDep,
    metadata_id: Annotated[int, Query(ge=1)],
) -> Response:
    factory = runtime.playback_factory
    if factory is None:
        raise HTTPException(status_code=503, detail="播放源未初始化")
    query = await _load_query(repo, metadata_id, None)
    listed = await factory.list_sources(query)
    items = [
        PlaybackSourceItem(
            source_id=row.source_id,
            name=row.name,
            content_type=row.content_type,
            seekable=row.seekable,
            available=row.available,
            key=row.key,
            detail=row.detail,
            href=_source_href(row.source_id, metadata_id, row.key, row.content_type),
            subtitles=[
                PlaybackSubtitleItem(
                    id=track.id,
                    label=track.label,
                    language=track.language,
                    href=subtitle_href(row.source_id, metadata_id, row.key, track.id),
                )
                for track in row.subtitles
            ],
        )
        for row in listed
    ]
    etag = _etag_for(items)
    cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=cache_headers)
    body = PlaybackSourceListResponse(items=items)
    return JSONResponse(content=body.model_dump(mode="json"), headers=cache_headers)


@router.get("/{source_id}/{metadata_id}/index.m3u8")
async def play_metadata_playlist(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _playlist(request, source_id, metadata_id, None, repo, runtime)


@router.head("/{source_id}/{metadata_id}/index.m3u8", include_in_schema=False)
async def play_metadata_playlist_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _playlist(request, source_id, metadata_id, None, repo, runtime)


@router.get("/{source_id}/{metadata_id}/streams/{key}/index.m3u8")
async def play_stream_playlist(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _playlist(request, source_id, metadata_id, key, repo, runtime)


@router.head("/{source_id}/{metadata_id}/streams/{key}/index.m3u8", include_in_schema=False)
async def play_stream_playlist_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _playlist(request, source_id, metadata_id, key, repo, runtime)


@router.get("/{source_id}/{metadata_id}/hls/{token}")
async def play_metadata_hls_part(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    token: Annotated[str, Path(pattern=_TOKEN_PATTERN)],
    runtime: RuntimeDep,
) -> Response:
    return await _hls_part(request, source_id, metadata_id, None, token, runtime)


@router.head("/{source_id}/{metadata_id}/hls/{token}", include_in_schema=False)
async def play_metadata_hls_part_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    token: Annotated[str, Path(pattern=_TOKEN_PATTERN)],
    runtime: RuntimeDep,
) -> Response:
    return await _hls_part(request, source_id, metadata_id, None, token, runtime)


@router.get("/{source_id}/{metadata_id}/streams/{key}/hls/{token}")
async def play_stream_hls_part(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    token: Annotated[str, Path(pattern=_TOKEN_PATTERN)],
    runtime: RuntimeDep,
) -> Response:
    return await _hls_part(request, source_id, metadata_id, key, token, runtime)


@router.head("/{source_id}/{metadata_id}/streams/{key}/hls/{token}", include_in_schema=False)
async def play_stream_hls_part_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    token: Annotated[str, Path(pattern=_TOKEN_PATTERN)],
    runtime: RuntimeDep,
) -> Response:
    return await _hls_part(request, source_id, metadata_id, key, token, runtime)


@router.get("/{source_id}/{metadata_id}/subtitles/{track_id}")
async def play_metadata_subtitle(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    track_id: Annotated[str, Path(pattern=_TRACK_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _subtitle(request, source_id, metadata_id, None, track_id, repo, runtime)


@router.get("/{source_id}/{metadata_id}/streams/{key}/subtitles/{track_id}")
async def play_stream_subtitle(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    track_id: Annotated[str, Path(pattern=_TRACK_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _subtitle(request, source_id, metadata_id, key, track_id, repo, runtime)


@router.get("/{source_id}/{metadata_id}")
async def play_metadata(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, None, repo, runtime)


@router.head("/{source_id}/{metadata_id}", include_in_schema=False)
async def play_metadata_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, None, repo, runtime)


@router.get("/{source_id}/{metadata_id}/streams/{key}")
async def play_stream(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, key, repo, runtime)


@router.head("/{source_id}/{metadata_id}/streams/{key}", include_in_schema=False)
async def play_stream_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    key: Annotated[str, Path(pattern=_KEY_PATTERN)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, key, repo, runtime)


async def _require_factory(source_id: str, runtime: RuntimeDep) -> PlaybackFactory:
    if len(source_id) > SOURCE_ID_MAX_LEN:
        raise HTTPException(status_code=404, detail="播放源不存在")
    factory = runtime.playback_factory
    if factory is None:
        raise HTTPException(status_code=503, detail="播放源未初始化")
    if not factory.known_source(source_id) or not factory.enabled(source_id):
        raise HTTPException(status_code=404, detail="播放源不存在")
    return factory


async def _playlist(
    request: Request,
    source_id: str,
    metadata_id: int,
    selected_key: str | None,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    factory = await _require_factory(source_id, runtime)
    query = await _load_query(repo, metadata_id, selected_key)
    try:
        text = await factory.hls_playlist_text(source_id, query)
    except LookupError:
        raise HTTPException(status_code=404, detail="没有可播放的流") from None
    except SourceError as exc:
        raise _playback_http_error(exc) from exc
    return factory.playlist_response(text, head=request.method == "HEAD")


async def _hls_part(
    request: Request,
    source_id: str,
    metadata_id: int,
    selected_key: str | None,
    token: str,
    runtime: RuntimeDep,
) -> Response:
    factory = await _require_factory(source_id, runtime)
    try:
        return await factory.serve_hls_part(
            request,
            source_id=source_id,
            metadata_id=metadata_id,
            selected_key=selected_key,
            token=token,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="播放片段不存在") from None
    except SourceError as exc:
        raise _playback_http_error(exc) from exc


async def _subtitle(
    request: Request,
    source_id: str,
    metadata_id: int,
    selected_key: str | None,
    track_id: str,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    factory = await _require_factory(source_id, runtime)
    query = await _load_query(repo, metadata_id, selected_key)
    try:
        result = await factory.load_subtitle(source_id, query, track_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="字幕不存在") from None
    except SourceError as exc:
        raise _playback_http_error(exc) from exc
    if isinstance(result, str):
        return Response(
            content=result.encode("utf-8"),
            media_type="text/vtt; charset=utf-8",
            headers={**NOSNIFF, "Cache-Control": PLAYLIST_CACHE_CONTROL},
        )
    return await factory.stream.proxy(request, source_id=source_id, target=result, allow="subtitle")


async def _play(
    request: Request,
    source_id: str,
    metadata_id: int,
    selected_key: str | None,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    factory = await _require_factory(source_id, runtime)
    query = await _load_query(repo, metadata_id, selected_key)
    try:
        target = await factory.resolve(source_id, query)
    except LookupError:
        raise HTTPException(status_code=404, detail="没有可播放的流") from None
    except SourceError as exc:
        raise _playback_http_error(exc) from exc
    if isinstance(target, FilePlaybackTarget):
        # 目标路径已由 ``PlaybackFactory.resolve`` 核对为条目索引内的文件.
        return IndexedFileResponse(
            request,
            target.path,
            media_type=target.content_type,
            headers={**NOSNIFF, "Cache-Control": "private"},
        )
    if isinstance(target, UpstreamPlaybackTarget):
        return await factory.stream.proxy(request, source_id=source_id, target=target)
    try:
        text = await factory.hls_playlist_text(source_id, query, target)
    except SourceError as exc:
        raise _playback_http_error(exc) from exc
    return factory.playlist_response(text, head=request.method == "HEAD")
