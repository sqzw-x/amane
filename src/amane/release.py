"""GitHub Releases 版本检查. 进程内 ETag + 1h 缓存; 失败不抛, 由调用方展示.

本体与 APP 各自发版 (`v*` 与 `app-*`), 因此只看 `/releases/latest` 是不够的: 它按创建时间返回最近一条,
APP 发布之后就会拿到 APP 的 tag, 解析不出版本, 更新提示随之失效. 这里取发布列表, 跳过非版本 tag,
再按版本号取最大的一条.
"""

from __future__ import annotations

import time
from typing import Any

import httpx2 as httpx
from packaging.version import InvalidVersion, Version

from .version import get_version

GITHUB_RELEASES_URL = "https://api.github.com/repos/sqzw-x/amane/releases?per_page=30"
GITHUB_RELEASES_PAGE = "https://github.com/sqzw-x/amane/releases"
_CACHE_TTL_S = 3600.0
_TIMEOUT_S = 10.0


def _user_agent() -> str:
    return f"Amane/{get_version()} (+https://github.com/sqzw-x/amane)"


def _strip_v(tag: str) -> str:
    return tag[1:] if tag[:1] in "vV" else tag


def _release_version(tag: str) -> Version | None:
    """tag 解析成版本; 其它发布线的 tag (`app-1.0.0`) 返回 None."""
    try:
        return Version(_strip_v(tag))
    except InvalidVersion:
        return None


def is_newer(latest: str, current: str) -> bool:
    """比较 GitHub tag 与包版本; 任一侧解析不出都视为"不是更新" — 非版本 tag 不能变成更新提示."""
    left, right = _release_version(latest), _release_version(current)
    if left is None or right is None:
        return False
    return left > right


def pick_latest_release(body: Any) -> tuple[str, str | None] | None:
    """从发布响应里取版本号最大的那条, 返回 (tag, html_url).

    跳过解析不出版本的 tag. 响应既可以是发布列表, 也可以是单条发布 — `AMANE_UPDATE_URL` 可以指向镜像的
    `/releases/latest`, 那种地址只返回一条.
    """
    items = body if isinstance(body, list) else [body]
    best: tuple[Version, str, str | None] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag:
            continue
        version = _release_version(tag)
        if version is None:
            continue
        html_url = item.get("html_url")
        url = html_url if isinstance(html_url, str) and html_url else None
        if best is None or version > best[0]:
            best = (version, tag, url)
    if best is None:
        return None
    return best[1], best[2]


class ReleaseSnapshot:
    __slots__ = ("html_url", "latest")

    def __init__(self, latest: str | None, html_url: str | None) -> None:
        self.latest = latest
        self.html_url = html_url


class ReleaseChecker:
    """带 ETag 的发布列表查询. 由 AppRuntime 持有, 不参与 rebuild."""

    def __init__(self) -> None:
        self._etag: str | None = None
        self._snapshot = ReleaseSnapshot(None, None)
        self._cached_at: float = 0.0

    async def fetch(self, *, proxy: str | None = None, url: str | None = None) -> ReleaseSnapshot:
        now = time.monotonic()
        if self._snapshot.latest is not None and now - self._cached_at < _CACHE_TTL_S:
            return self._snapshot
        target = url.strip() if url else GITHUB_RELEASES_URL
        headers = {"User-Agent": _user_agent(), "Accept": "application/vnd.github+json"}
        if self._etag is not None:
            headers["If-None-Match"] = self._etag
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=_TIMEOUT_S) as client:
                resp = await client.get(target, headers=headers)
        except httpx.HTTPError:
            return self._stale_or_empty()
        if resp.status_code == 304:
            self._cached_at = now
            return self._snapshot
        if resp.status_code != 200:
            return self._stale_or_empty()
        try:
            body = resp.json()
        except ValueError:
            return self._stale_or_empty()
        selected = pick_latest_release(body)
        if selected is None:
            return self._stale_or_empty()
        tag, html_url = selected
        etag = resp.headers.get("etag")
        self._etag = etag if isinstance(etag, str) else None
        self._snapshot = ReleaseSnapshot(tag, html_url or GITHUB_RELEASES_PAGE)
        self._cached_at = now
        return self._snapshot

    def _stale_or_empty(self) -> ReleaseSnapshot:
        if self._snapshot.latest is not None:
            return self._snapshot
        return ReleaseSnapshot(None, None)
