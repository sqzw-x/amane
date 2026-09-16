"""离线 HTTP 回放; duck-type 兼容 WebClient 的 text/json 接口."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..net.errors import FailureKind, RequestError, RequestFailure
from .models import HttpExchangeMeta
from .recorder import load_http_index


@dataclass
class _ReplayResponse:
    """仅供内部 get_* 使用, 不暴露给爬虫."""

    status_code: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)
    encoding: str = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


@dataclass
class _Entry:
    meta: HttpExchangeMeta
    body: bytes | None
    consumed: bool = False


class ReplayWebClient:
    """按 method+url 匹配任务记录 http/ 中的条目; 无命中抛 RequestError."""

    def __init__(self, http_dir: Path, *, limiters: Any = None, proxy: str | None = None, **_: Any):
        self._proxy = proxy
        self._limiters = limiters
        self._entries = [_Entry(meta=m, body=b) for m, b in load_http_index(http_dir)]

    async def close(self) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        use_proxy: bool = True,
        timeout: float | None = None,
        allow_redirects: bool = True,
        ok_statuses: frozenset[int] | None = None,
    ) -> _ReplayResponse:
        entry = self._match(method, url)
        if entry is None:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"replay miss: {method.upper()} {url}")
            )
        entry.consumed = True
        if entry.meta.error and entry.body is None:
            raise RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message=entry.meta.error))
        status = entry.meta.status or 200
        extra_ok = ok_statuses or frozenset()
        if status >= 400 and status not in extra_ok:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.HTTP_STATUS, status=status, message=f"HTTP {status}")
            )
        body = entry.body or b""
        ct = entry.meta.content_type or "application/octet-stream"
        return _ReplayResponse(status_code=status, content=body, headers={"Content-Type": ct})

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> str:
        resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        resp.encoding = encoding
        return resp.text

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> Any:
        resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        try:
            return resp.json()
        except Exception as e:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"JSON parse error: {e}")
            ) from e

    async def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> bytes:
        resp = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        return resp.content

    async def post_text(
        self,
        url: str,
        *,
        data: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> str:
        resp = await self.request(
            "POST", url, data=data, json=json, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        resp.encoding = encoding
        return resp.text

    async def post_json(
        self,
        url: str,
        *,
        data: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> Any:
        resp = await self.request(
            "POST", url, data=data, json=json, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        try:
            return resp.json()
        except Exception as e:
            raise RequestError(
                url, RequestFailure(kind=FailureKind.UNEXPECTED, message=f"JSON parse error: {e}")
            ) from e

    async def get_filesize(self, url: str, *, use_proxy: bool = True) -> int | None:
        try:
            resp = await self.request("HEAD", url, use_proxy=use_proxy)
        except RequestError:
            return None
        return len(resp.content)

    async def resolve_final_url(self, url: str, *, use_proxy: bool = True) -> str:
        # 录制只存请求 URL, 不含重定向终址; 回放按原 URL 处理.
        return url

    async def download(self, url: str, dest: Path, **kwargs: Any) -> bool:
        try:
            data = await self.get_bytes(url)
        except RequestError:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    def _match(self, method: str, url: str) -> _Entry | None:
        method_u = method.upper()
        # 精确 method+url 且未消费
        for e in self._entries:
            if not e.consumed and e.meta.method == method_u and e.meta.url == url:
                return e
        # 忽略 query 顺序差异: 同 path+method
        target = urlparse(url)
        for e in self._entries:
            if e.consumed or e.meta.method != method_u:
                continue
            p = urlparse(e.meta.url)
            if p.scheme == target.scheme and p.netloc == target.netloc and p.path == target.path:
                return e
        return None
