"""本地文件按 url hash 直出 (无选源/裁剪/超分).
SR 可能就地覆盖 (URL 不变, 字节变), 须 ETag + no-cache 再验证.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ...net.egress import EgressPolicy
from ...net.errors import EgressRejected, RequestError
from ..deps import RuntimeDep
from ..support.http_cache import RESOURCE_CACHE_HEADERS, format_etag, maybe_not_modified
from ..support.media_type import media_type_and_headers

logger = structlog.get_logger()

router = APIRouter(prefix="/resources", tags=["resources"])

_PROXY_EGRESS = EgressPolicy()
"""代理端点只接受公网 http(s): URL 来自外站元数据, 私网地址即 SSRF 入口, 重定向逐跳复查."""

_PROXY_MAX_BYTES = 32 * 1024 * 1024
"""代理图片的正文上限. 超过即中止下载, 不把上游自称的长度当作可信边界."""


@router.get("/proxy")
async def proxy_image(
    runtime: RuntimeDep, request: Request, url: Annotated[str, Query(description="External image URL to proxy")]
) -> Response:
    """上游失败记入进程内负缓存 (15 分钟), 窗口内同 URL 直接 502.
    同 URL 进行中请求合并为一次 acquire.
    """
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="URL 无法解析") from e
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="仅支持 http/https URL")

    failures = runtime.proxy_failure_cache
    try:
        await _PROXY_EGRESS.ensure_allowed(url)
    except EgressRejected as e:
        logger.warning("proxy image address rejected", url=url, reason=e.message)
        raise HTTPException(status_code=400, detail="仅支持公网地址") from e
    except RequestError as e:
        # 解析失败是上游不可达, 不是地址不合规: 与下载失败同样返回 502 并进入负缓存.
        logger.warning("proxy image host unreachable", url=url, reason=e.message)
        failures.remember(url)
        raise HTTPException(status_code=502, detail="上游图片获取失败") from e

    # 负缓存命中则直接 502
    if failures.is_blocked(url):
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 同 URL 进行中的 acquire 合并为一次; 并发额度只由真正发起下载的那一方持有,
    # 否则等待同一 URL 的请求也会占额度.
    store = runtime.resource_store

    async def _acquire() -> Path | None:
        async with runtime.proxy_downloads:
            return await store.acquire(url, runtime.web_client, egress=_PROXY_EGRESS, max_bytes=_PROXY_MAX_BYTES)

    local_path = await failures.coalesce(url, _acquire)
    if local_path is None:
        failures.remember(url)
        logger.warning("proxy image acquire failed", url=url)
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 成功后清除负缓存
    failures.forget(url)

    record = await store.get_by_url(url)
    content_hash = record.content_hash if record is not None else None
    not_modified = maybe_not_modified(request, content_hash)
    if not_modified is not None:
        return not_modified

    media_type, headers = media_type_and_headers(local_path)
    headers.update(RESOURCE_CACHE_HEADERS)
    if content_hash:
        headers["ETag"] = format_etag(content_hash)
    return FileResponse(path=local_path, media_type=media_type, headers=headers)


@router.get("/{url_hash}")
async def serve_resource(url_hash: str, request: Request, runtime: RuntimeDep) -> Response:
    found = await runtime.resource_store.get_by_url_hash(url_hash)
    if found is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    record, path = found

    not_modified = maybe_not_modified(request, record.content_hash)
    if not_modified is not None:
        return not_modified

    media_type, headers = media_type_and_headers(path)
    headers.update(RESOURCE_CACHE_HEADERS)
    if record.content_hash:
        headers["ETag"] = format_etag(record.content_hash)
    return FileResponse(path=path, media_type=media_type, headers=headers)
