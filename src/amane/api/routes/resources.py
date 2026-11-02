"""本地文件按 url hash 直出 (无选源/裁剪/超分).
SR 可能就地覆盖 (URL 不变, 字节变), 须 ETag + no-cache 再验证.
另收本地上传的图片: 按内容判定格式后存成 Resource, 供影片的图片字段引用.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from ...media import MAX_IMAGE_UPLOAD_BYTES, store_uploaded_image
from ..deps import RuntimeDep
from ..models import ResourceUploadResponse
from ..support.http_cache import RESOURCE_CACHE_HEADERS, format_etag, maybe_not_modified

logger = structlog.get_logger()

router = APIRouter(prefix="/resources", tags=["resources"])

# 上传按块读入, 超限立刻中断: 一次性 read() 会把任意大的请求体整个吃进内存.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
}


def _image_response(request: Request, path: Path, content_hash: str | None) -> Response:
    not_modified = maybe_not_modified(request, content_hash)
    if not_modified is not None:
        return not_modified

    headers = dict(RESOURCE_CACHE_HEADERS)
    if content_hash:
        headers["ETag"] = format_etag(content_hash)
    return FileResponse(path=path, media_type=_EXT_TO_MIME.get(path.suffix.lower(), "image/jpeg"), headers=headers)


async def _read_upload(file: UploadFile, limit: int) -> bytes | None:
    """按块读到 ``limit`` 字节为止; 超限返回 None, 不再继续读."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload")
async def upload_resource(
    runtime: RuntimeDep,
    file: Annotated[UploadFile, File(description="JPEG / PNG / WebP 图片")],
) -> ResourceUploadResponse:
    """保存本地上传的图片, 返回可写入图片字段的内部 URL.

    只认内容不认文件名与 Content-Type; 同一份字节重复上传复用同一条资源.
    """
    data = await _read_upload(file, MAX_IMAGE_UPLOAD_BYTES)
    if data is None:
        raise HTTPException(
            status_code=413,
            detail=f"图片不能超过 {MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    try:
        url = await store_uploaded_image(data, runtime.resource_store)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info("resource uploaded", url=url, size=len(data))
    return ResourceUploadResponse(url=url)


@router.get("/proxy")
async def proxy_image(
    runtime: RuntimeDep, request: Request, url: Annotated[str, Query(description="External image URL to proxy")]
) -> Response:
    """上游失败记入进程内负缓存 (15 分钟), 窗口内同 URL 直接 502.
    同 URL 进行中请求合并为一次 acquire.
    使用图片 token 的请求只从缓存读取资源: 未命中返回 404, 不发起对外请求, 不写入负缓存.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="仅支持 http/https URL")

    store = runtime.resource_store
    if request.state.via_image_token:
        local_path = await store.resolve(url)
        if local_path is None:
            raise HTTPException(status_code=404, detail="资源不在本地缓存")
        record = await store.get_by_url(url)
        return _image_response(request, local_path, record.content_hash if record is not None else None)

    # 负缓存命中则直接 502
    failures = runtime.proxy_failure_cache
    if failures.is_blocked(url):
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 同 URL 进行中的 acquire 合并为一次
    local_path = await failures.coalesce(url, lambda: store.acquire(url, runtime.web_client))
    if local_path is None:
        failures.remember(url)
        logger.warning("proxy image acquire failed", url=url)
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 成功后清除负缓存
    failures.forget(url)

    record = await store.get_by_url(url)
    return _image_response(request, local_path, record.content_hash if record is not None else None)


@router.get("/{url_hash}")
async def serve_resource(url_hash: str, request: Request, runtime: RuntimeDep) -> Response:
    found = await runtime.resource_store.get_by_url_hash(url_hash)
    if found is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    record, path = found

    not_modified = maybe_not_modified(request, record.content_hash)
    if not_modified is not None:
        return not_modified

    headers = dict(RESOURCE_CACHE_HEADERS)
    if record.content_hash:
        headers["ETag"] = format_etag(record.content_hash)
    media_type = record.mime_type or _EXT_TO_MIME.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path=path, media_type=media_type, headers=headers)
