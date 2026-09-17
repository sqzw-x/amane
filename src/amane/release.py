"""GitHub Releases 版本检查. 进程内 ETag + 1h 缓存; 失败不抛, 由调用方展示.

本体与 APP 各自发版 (`v*` 与 `app-*`), 因此只看 `/releases/latest` 是不够的: 它按创建时间返回最近一条,
APP 发布之后就会拿到 APP 的 tag, 解析不出版本, 更新提示随之失效. 这里取发布列表, 跳过非版本 tag 与
草稿 / 预发布 (前者是别条发布线, 后者不该提示给正式版用户), 再按版本号取最大的一条.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx2 as httpx
from packaging.version import InvalidVersion, Version

from .version import get_version

GITHUB_RELEASES_URL = "https://api.github.com/repos/sqzw-x/amane/releases?per_page=100"
GITHUB_RELEASES_PAGE = "https://github.com/sqzw-x/amane/releases"
logger = logging.getLogger(__name__)

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

    跳过解析不出版本的 tag (`app-*` 那条发布线) 与草稿 / 预发布:`/releases/latest` 本身就不返回后两者,
    只看列表时必须自己补上, 否则维护者发一个 `v0.17.0-rc1` 会让正式版用户收到更新提示.

    响应既可以是发布列表, 也可以是单条发布 — `AMANE_UPDATE_URL` 可以指向镜像的 `/releases/latest`.
    """
    items = body if isinstance(body, list) else [body]
    best: tuple[Version, str, str | None] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("draft") is True or item.get("prerelease") is True:
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


class _Entry:
    """一个检查地址上的状态: 快照、它的 ETag 与取回时间."""

    __slots__ = ("cached_at", "etag", "snapshot")

    def __init__(self) -> None:
        self.snapshot = ReleaseSnapshot(None, None)
        self.etag: str | None = None
        self.cached_at: float = 0.0

    def stale_or_empty(self) -> ReleaseSnapshot:
        if self.snapshot.latest is not None:
            return self.snapshot
        return ReleaseSnapshot(None, None)


class ReleaseChecker:
    """带 ETag 的发布列表查询. 由 AppRuntime 持有, 不参与 rebuild.

    状态按地址分开记: `AMANE_UPDATE_URL` 可以在镜像与官方 API 之间切换, 共用一个 ETag 会把 A 的条件请求
    发给 B, B 若原样透传就会回 304, 于是永远停在旧快照上.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    async def fetch(self, *, proxy: str | None = None, url: str | None = None) -> ReleaseSnapshot:
        target = url.strip() if url else GITHUB_RELEASES_URL
        entry = self._entries.setdefault(target, _Entry())
        now = time.monotonic()
        if entry.snapshot.latest is not None and now - entry.cached_at < _CACHE_TTL_S:
            return entry.snapshot
        headers = {"User-Agent": _user_agent(), "Accept": "application/vnd.github+json"}
        if entry.etag is not None:
            headers["If-None-Match"] = entry.etag
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=_TIMEOUT_S) as client:
                resp = await client.get(target, headers=headers)
        except httpx.HTTPError:
            return entry.stale_or_empty()
        if resp.status_code == 304:
            entry.cached_at = now
            return entry.snapshot
        if resp.status_code != 200:
            # "拉不到" 与 "没有更新" 对调用方是同一个空快照, 因此在这里留一条日志区分两者.
            logger.info("release check: %s answered HTTP %s", target, resp.status_code)
            return entry.stale_or_empty()
        try:
            body = resp.json()
        except ValueError:
            return entry.stale_or_empty()
        selected = pick_latest_release(body)
        if selected is None:
            entries = len(body) if isinstance(body, list) else 1
            logger.info("release check: %s has no version tag (%d entries)", target, entries)
            return entry.stale_or_empty()
        tag, html_url = selected
        etag = resp.headers.get("etag")
        entry.etag = etag if isinstance(etag, str) else None
        entry.snapshot = ReleaseSnapshot(tag, html_url or GITHUB_RELEASES_PAGE)
        entry.cached_at = now
        return entry.snapshot
