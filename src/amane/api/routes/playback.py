from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse

from ...net.errors import SourceError
from ...playback.factory import SOURCE_ID_MAX_LEN
from ...playback.local import LOCAL_SOURCE_ID
from ...playback.query import playback_query
from ...plugins.api import FilePlaybackTarget, PlaybackQuery, UpstreamPlaybackTarget
from ..deps import RepoDep, RuntimeDep
from ..models.playback import PlaybackSourceItem, PlaybackSourceListResponse

router = APIRouter(prefix="/playback", tags=["playback"])


def _stream_href(source_id: str, metadata_id: int, media_file_id: int | None) -> str:
    base = f"/api/playback/{source_id}/{metadata_id}"
    if media_file_id is None:
        return base
    return f"{base}/files/{media_file_id}"


def _etag_for(items: list[PlaybackSourceItem]) -> str:
    payload = json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


async def _load_query(
    repo: RepoDep,
    metadata_id: int,
    media_file_id: int | None,
) -> PlaybackQuery:
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="元数据不存在")
    files = await repo.get_media_by_metadata_id(metadata_id)
    if media_file_id is not None and not any(item.id == media_file_id for item in files):
        raise HTTPException(status_code=404, detail="文件不属于该元数据")
    return playback_query(metadata, files, selected_file_id=media_file_id)


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
            media_file_id=row.media_file_id,
            detail=row.detail,
            href=_stream_href(row.source_id, metadata_id, row.media_file_id),
        )
        for row in listed
        if row.available
    ]
    etag = _etag_for(items)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    body = PlaybackSourceListResponse(items=items)
    return JSONResponse(content=body.model_dump(mode="json"), headers={"ETag": etag})


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


@router.get("/{source_id}/{metadata_id}/files/{media_file_id}")
async def play_metadata_file(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    media_file_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, media_file_id, repo, runtime)


@router.head("/{source_id}/{metadata_id}/files/{media_file_id}", include_in_schema=False)
async def play_metadata_file_head(
    request: Request,
    source_id: str,
    metadata_id: Annotated[int, Path(ge=1)],
    media_file_id: Annotated[int, Path(ge=1)],
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    return await _play(request, source_id, metadata_id, media_file_id, repo, runtime)


async def _play(
    request: Request,
    source_id: str,
    metadata_id: int,
    media_file_id: int | None,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> Response:
    if len(source_id) > SOURCE_ID_MAX_LEN:
        raise HTTPException(status_code=404, detail="播放源不存在")
    factory = runtime.playback_factory
    if factory is None:
        raise HTTPException(status_code=503, detail="播放源未初始化")
    if not factory.known_source(source_id) or not factory.enabled(source_id):
        raise HTTPException(status_code=404, detail="播放源不存在")
    query = await _load_query(repo, metadata_id, media_file_id)
    try:
        target = await factory.resolve(source_id, query)
    except LookupError:
        raise HTTPException(status_code=404, detail="没有可播放的流") from None
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=exc.detail or "上游失败") from exc
    if isinstance(target, FilePlaybackTarget):
        if source_id != LOCAL_SOURCE_ID:
            raise HTTPException(status_code=502, detail="插件不得返回本地文件")
        return FileResponse(
            path=target.path,
            media_type=target.content_type,
        )
    if isinstance(target, UpstreamPlaybackTarget):
        return await factory.stream.proxy(request, source_id=source_id, target=target)
    raise HTTPException(status_code=502, detail="不支持的播放目标")
