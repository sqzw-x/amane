"""输出条目已索引的本机文件, 并在客户端断开后停止读取.

``starlette.responses.FileResponse`` 不监听 ``http.disconnect``: ASGI 服务器在客户端离开后
让 ``send`` 静默返回, 读取循环因此读到文件末尾. 文件位于网盘挂载时, 用户已经离开页面, 这些
读取仍持续消耗上游流量. 本模块在每次读取之前检查断连, 断开即结束循环并在 ``finally`` 中关闭
文件句柄.
"""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Mapping
from email.utils import formatdate
from hashlib import md5
from os import PathLike
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

CHUNK_SIZE = 256 * 1024
_UNREADABLE_DETAIL = "条目索引的文件当前无法读取"


def _open_binary(path: Path) -> BinaryIO:
    """在线程池中打开文件.

    单独成函数是为了让类型检查看到字面量 ``"rb"`` 对应的重载; 直接传 ``Path.open`` 与参数
    元组时会退化成「文本或二进制」的联合类型.
    """
    return path.open("rb")


class _MalformedRange(Exception):
    """``Range`` 头语法无法解析."""


class _UnsatisfiableRange(Exception):
    """``Range`` 头语法合法, 但与文件大小不相交."""


def _etag(mtime: float, size: int) -> str:
    return f'"{md5(f"{mtime}-{size}".encode(), usedforsecurity=False).hexdigest()}"'


def _is_multi_range(range_header: str) -> bool:
    spec = range_header.strip()
    if not spec.casefold().startswith("bytes="):
        return False
    return "," in spec.split("=", 1)[1]


def _parse_single_range(range_header: str, size: int) -> tuple[int, int]:
    """解析单段字节范围, 返回闭区间 ``(start, end)``.

    多段 Range 由调用方先行拒绝: 播放器不做多段请求, 组包 multipart/byteranges 只增加出错
    面, 上游代理路径同样以 400 拒绝多段.
    """
    if size <= 0:
        raise _UnsatisfiableRange
    unit, separator, spans = range_header.strip().partition("=")
    if not separator or unit.strip().casefold() != "bytes":
        raise _MalformedRange
    first, _, last = spans.partition("-")
    try:
        if not first:
            # 后缀范围: 请求最后 N 字节.
            suffix = int(last)
            if suffix <= 0:
                raise _UnsatisfiableRange
            return max(size - suffix, 0), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError as exc:
        raise _MalformedRange from exc
    if start >= size or end < start:
        raise _UnsatisfiableRange
    return start, min(end, size - 1)


def _raw_headers(headers: Mapping[str, str]) -> list[tuple[bytes, bytes]]:
    return [(key.casefold().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()]


class IndexedFileResponse(Response):
    """输出条目已索引的本机文件.

    发往浏览器的响应带 ``Accept-Ranges: bytes``; 单段 ``Range`` 返回 206 与 ``Content-Range``,
    不可满足的范围返回 416 与 ``Content-Range: bytes */<size>``, 多段 ``Range`` 与语法无法解析
    的 ``Range`` 返回 400, ``HEAD`` 只发送响应头. 不写 ``Content-Disposition: attachment`` ——
    浏览器会因此下载而不是播放.

    路径合法性由调用方在 ``PlaybackFactory.resolve`` 中核对, 这里只处理读取期失效: 文件消失
    或不再是普通文件时返回 502, 而不是 500.
    """

    chunk_size = CHUNK_SIZE

    def __init__(
        self,
        request: Request,
        path: str | PathLike[str],
        *,
        media_type: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._request = request
        self._path = Path(path)
        self._media_type = media_type
        self._base_headers = dict(headers or {})
        super().__init__(media_type=media_type, headers=self._base_headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        size, mtime = await asyncio.to_thread(self._stat)
        etag = _etag(mtime, size)
        last_modified = formatdate(mtime, usegmt=True)
        base = {
            **{key.casefold(): value for key, value in self._base_headers.items()},
            "content-type": self._media_type,
            "accept-ranges": "bytes",
            "etag": etag,
            "last-modified": last_modified,
        }

        start, end, status = 0, size - 1, 200
        range_header = self._request.headers.get("range")
        if range_header is not None and self._use_range(etag, last_modified):
            if _is_multi_range(range_header):
                raise HTTPException(status_code=400, detail="不支持多段 Range")
            try:
                start, end = _parse_single_range(range_header, size)
            except _MalformedRange as exc:
                raise HTTPException(status_code=400, detail="Range 无法解析") from exc
            except _UnsatisfiableRange:
                unsatisfiable = Response(
                    status_code=416,
                    headers={**base, "content-range": f"bytes */{size}", "content-length": "0"},
                )
                await unsatisfiable(scope, receive, send)
                return
            status = 206

        length = max(end - start + 1, 0)
        headers = {**base, "content-length": str(length)}
        if status == 206:
            headers["content-range"] = f"bytes {start}-{end}/{size}"

        header_only = scope["type"] == "http" and scope["method"].upper() == "HEAD"
        handle = None if header_only or length == 0 else await self._open()
        await send({"type": "http.response.start", "status": status, "headers": _raw_headers(headers)})
        try:
            if handle is None:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await self._stream(handle, start, length, send)
        finally:
            if handle is not None:
                await asyncio.to_thread(handle.close)
        if self.background is not None:
            await self.background()

    async def _stream(self, handle: BinaryIO, start: int, length: int, send: Send) -> None:
        """按块发送正文; 客户端断开或文件提前结束时立即返回."""
        await asyncio.to_thread(handle.seek, start)
        remaining = length
        while remaining > 0:
            # 断开后不再向网盘发起读取: 每次读取之前检查.
            if await self._request.is_disconnected():
                return
            chunk = await asyncio.to_thread(handle.read, min(self.chunk_size, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": remaining > 0})

    def _stat(self) -> tuple[int, float]:
        """读取文件状态; 调用方已核对路径属于条目索引, 此处失败只可能是读取期失效."""
        try:
            info = self._path.stat()
        except OSError as exc:
            raise HTTPException(status_code=502, detail=_UNREADABLE_DETAIL) from exc
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(status_code=502, detail=_UNREADABLE_DETAIL)
        return info.st_size, info.st_mtime

    async def _open(self) -> BinaryIO:
        try:
            return await asyncio.to_thread(_open_binary, self._path)
        except OSError as exc:
            raise HTTPException(status_code=502, detail=_UNREADABLE_DETAIL) from exc

    def _use_range(self, etag: str, last_modified: str) -> bool:
        """``If-Range`` 与当前校验值一致时才使用 Range; 不一致时按完整正文输出."""
        header = self._request.headers.get("if-range")
        if header is None:
            return True
        return header.strip() in {etag, last_modified}
