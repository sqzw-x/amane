"""按艺名查 gFriends Filetree 头像 URL."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override
from urllib.parse import quote, urlsplit

from amane.enums import ActorGender, SiteName
from amane.net.connectivity import ConnectivityOutcome, probe_get
from amane.net.errors import SourceError
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ..base import ActorCrawler
from ..models import ActorMetadata

_DEFAULT_REPO = "https://github.com/gfriends/gfriends"
_CHECK_INTERVAL_S = 24 * 3600
"""索引校验间隔. 到期后用 HEAD 比对 ETag, 一致时只更新校验时间, 不下载正文."""
_RETRY_INTERVAL_S = 3600
"""校验或下载失败后的退避间隔, 解析出空索引按失败处理. 期间沿用旧索引, 避免上游不可达时每个演员的查找各触发一次超时."""


@dataclass(frozen=True, slots=True)
class GFriendsImage:
    url: str
    path: str  # Content/{folder}/{file}


class GFriendsActorCrawler(ActorCrawler):
    def __init__(self, client: Any, config: Any = None, *, data_dir: Path | None = None, repo_url: str | None = None):
        super().__init__(client, config)
        self._data_dir = data_dir
        self._repo_url = (repo_url or _DEFAULT_REPO).rstrip("/")
        self._index: dict[str, GFriendsImage] | None = None
        self._index_at = 0.0  # 索引成为当前值的时间; 缓存来源取文件 mtime, 与校验间隔同一时钟
        self._retry_at = 0.0  # 失败后允许重试的时间, 退避期内直接用旧索引
        self._refresh_lock = asyncio.Lock()  # 并发 fetch 只让一个去比对与下载

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.GFRIENDS,
            base_url="https://raw.githubusercontent.com/gfriends/gfriends/master",
            urls=[
                "https://raw.githubusercontent.com",
                "https://github.com/gfriends/gfriends",
            ],
            capabilities=frozenset({SourceCapability.ACTOR_IMAGE}),
            genders=frozenset({ActorGender.FEMALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        index = await self._ensure_index()
        hit = _lookup(index, name)
        if not hit:
            return None
        return ActorMetadata(
            name=name,
            image_urls=[hit.url],
            provider_ids={"gfriends": name},
            source_url=_blob_url(self._repo_url, hit.path),
            content_path=hit.path,
        )

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """真实入口是仓库根下的 ``Filetree.json``: 仓库目录本身 404, 探测 ``base_url`` 会假报不可达."""
        return await probe_get(self.client.web_client, self._tree_url(), cookies=self.cookies, headers=self.headers)

    def _tree_url(self) -> str:
        return f"{self._raw_base()}/Filetree.json"

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError

    async def _ensure_index(self) -> dict[str, GFriendsImage]:
        """到期后与上游比对 ETag: 一致时只更新校验时间, 不同或无法比对时重新下载."""
        if (index := self._usable_index()) is not None:
            return index
        async with self._refresh_lock:
            # 等锁期间可能已被并发调用刷新完, 重新判定一次.
            if (index := self._usable_index()) is not None:
                return index
            return await self._refresh()

    def _usable_index(self) -> dict[str, GFriendsImage] | None:
        """内存索引可用时返回它: 未到校验间隔, 或失败后在退避间隔内."""
        if self._index is None:
            return None
        now = time.time()
        if now - self._index_at < _CHECK_INTERVAL_S or now < self._retry_at:
            return self._index
        return None

    async def _refresh(self) -> dict[str, GFriendsImage]:
        now = time.time()
        cached = self._read_cache()
        if cached is not None and not self._cache_matches_source():
            cached = None  # 换过仓库地址: 缓存里是旧仓库的绝对地址
        if cached is not None:
            index, mtime = cached
            if now - mtime < _CHECK_INTERVAL_S:
                self._index, self._index_at = index, mtime
                return index

        # 先取 ETag 再下载: 记录下来的值不会比索引内容新, 上游在两者之间变化只多下载一次.
        current = await self._probe_etag()
        local_etag = self._read_etag() if cached is not None else None
        if cached is not None and local_etag is not None and local_etag == current:
            self._touch_cache()
            self._index, self._index_at = cached[0], now
            return cached[0]

        try:
            tree = await self.client.get_json(self._tree_url(), cookies=self.cookies)
        except SourceError as exc:
            stale = self._stale_index(cached)
            if stale is None:
                raise
            self.logger.warning("gfriends index refresh failed, using stale index", error=str(exc))
            return self._use_stale(stale, now)

        index = flatten_filetree(tree, raw_base=self._raw_base()) if isinstance(tree, dict) else {}
        if not index:
            # 空索引多为上游改版或异常应答: 按失败处理, 否则整个进程一整个间隔都没有头像.
            stale = self._stale_index(cached)
            if stale is not None:
                self.logger.warning("gfriends index is empty, using stale index")
                return self._use_stale(stale, now)
            self.logger.warning("gfriends index is empty")
            self._index = index
            self._retry_at = now + _RETRY_INTERVAL_S
            return index

        self._index, self._index_at, self._retry_at = index, now, 0.0
        if self._write_cache(index):
            # 与索引一起写入: 索引没写成却更新 ETag, 下一次比对会把旧索引当成最新.
            self._write_etag(current)
        return index

    def _stale_index(self, cached: tuple[dict[str, GFriendsImage], float] | None) -> dict[str, GFriendsImage] | None:
        if self._index:
            return self._index
        return cached[0] if cached is not None else None

    def _use_stale(self, stale: dict[str, GFriendsImage], now: float) -> dict[str, GFriendsImage]:
        self._index = stale
        self._retry_at = now + _RETRY_INTERVAL_S
        return stale

    async def _probe_etag(self) -> str | None:
        """用 HEAD 取当前 ETag; 站点不支持 HEAD 或请求失败时返回 ``None``, 调用方退回重新下载.

        ``max_attempts=1``: 探测要回答的是「此刻是否变化」, 重试只会把同一个结论拖长.
        """
        try:
            resp = await self.client.web_client.request(
                "HEAD", self._tree_url(), headers=dict(self.headers), cookies=self.cookies, max_attempts=1
            )
        except SourceError:
            return None
        current = resp.headers.get("etag")
        return current if isinstance(current, str) and current else None

    def _raw_base(self) -> str:
        # https://github.com/gfriends/gfriends → raw .../master
        if "github.com" in self._repo_url and "raw.githubusercontent.com" not in self._repo_url:
            path = self._repo_url.split("github.com/", 1)[-1]
            return f"https://raw.githubusercontent.com/{path}/master"
        return self.base_url

    def _read_cache(self) -> tuple[dict[str, GFriendsImage], float] | None:
        cache = self._cache_path()
        if cache is None or not cache.exists():
            return None
        try:
            mtime = cache.stat().st_mtime
            raw = json.loads(cache.read_text(encoding="utf-8"))
        except OSError, ValueError:  # ValueError 覆盖 JSONDecodeError 与非 UTF-8 内容
            self.logger.warning("gfriends cache unreadable", path=str(cache))
            return None
        if not isinstance(raw, dict):
            return None
        index = _index_from_cache(raw)
        return (index, mtime) if index else None

    def _write_cache(self, index: dict[str, GFriendsImage]) -> bool:
        cache = self._cache_path()
        if cache is None:
            return False
        return self._write_json(cache, {k: {"url": v.url, "path": v.path} for k, v in index.items()})

    def _write_etag(self, etag: str | None) -> None:
        path = self._etag_path()
        if path is None:
            return
        self._write_json(path, {"url": self._tree_url(), "etag": etag})

    def _write_json(self, path: Path, payload: Any) -> bool:
        """写到同目录的临时文件再替换: 半截文件会让下一次读取退回重新下载."""
        tmp_name = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            Path(tmp_name).replace(path)
        except OSError:
            self.logger.warning("gfriends write failed", path=str(path))
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink(missing_ok=True)
            return False
        return True

    def _touch_cache(self) -> None:
        """把 mtime 记为本次校验时间, 校验间隔从校验算起而不是从写入算起."""
        cache = self._cache_path()
        if cache is None:
            return
        try:
            os.utime(cache)
        except OSError:
            self.logger.warning("gfriends cache touch failed", path=str(cache))

    def _etag_record(self) -> dict[str, Any] | None:
        path = self._etag_path()
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        return raw if isinstance(raw, dict) else None

    def _cache_matches_source(self) -> bool:
        """索引缓存是否属于当前 Filetree 地址; 没有来源记录时视为属于当前地址."""
        stored = self._stored_url()
        return stored is None or stored == self._tree_url()

    def _stored_url(self) -> str | None:
        """ETag 记录里的 Filetree 地址; 没有记录时返回 ``None``."""
        record = self._etag_record()
        if record is None:
            return None
        url = record.get("url")
        return url if isinstance(url, str) else None

    def _read_etag(self) -> str | None:
        record = self._etag_record()
        if record is None or record.get("url") != self._tree_url():
            return None  # 另一个地址的 ETag 不能拿来比对
        etag = record.get("etag")
        return etag if isinstance(etag, str) and etag else None

    def _cache_path(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "gfriends.json"

    def _etag_path(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "gfriends.etag"


def flatten_filetree(tree: dict[str, Any], *, raw_base: str) -> dict[str, GFriendsImage]:
    """Content 子目录按名字母序遍历; 同一文件名先出现者保留 (倾向非 z-* 低质源)."""
    content = tree.get("Content")
    if not isinstance(content, dict):
        return {}
    out: dict[str, GFriendsImage] = {}
    for folder in sorted(content.keys()):
        files = content[folder]
        if not isinstance(files, dict):
            continue
        for filename, value in files.items():
            if filename in out:
                continue
            # value 常为规范/AI-Fix 文件名, 可带 ``?t=``; 路径 = Content/{folder}/{target}.
            target = str(value).split("?", 1)[0] if value else str(filename)
            rel_path = f"Content/{folder}/{target}"
            encoded_folder = quote(str(folder), safe="")
            encoded_file = quote(target, safe="")
            url = f"{raw_base}/Content/{encoded_folder}/{encoded_file}"
            out[str(filename)] = GFriendsImage(url=url, path=rel_path)
    return out


def _lookup(index: dict[str, GFriendsImage], name: str) -> GFriendsImage | None:
    for ext in (".jpg", ".png", ".JPG", ".PNG"):
        key = f"{name}{ext}"
        if key in index:
            return index[key]
    return None


def _blob_url(repo_url: str, content_path: str) -> str:
    encoded = "/".join(quote(part, safe="") for part in content_path.split("/"))
    return f"{repo_url.rstrip('/')}/blob/master/{encoded}"


def _path_from_raw_url(url: str) -> str | None:
    path = urlsplit(url).path
    marker = "/Content/"
    idx = path.find(marker)
    if idx < 0:
        return None
    return path[idx + 1 :]  # drop leading /


def _index_from_cache(raw: dict[str, Any]) -> dict[str, GFriendsImage] | None:
    # 兼容 filename→url 字符串与 filename→{url,path}.
    out: dict[str, GFriendsImage] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            path = _path_from_raw_url(value) or ""
            out[str(key)] = GFriendsImage(url=value, path=path)
        elif isinstance(value, dict):
            url = value.get("url")
            path = value.get("path")
            if isinstance(url, str) and url:
                out[str(key)] = GFriendsImage(
                    url=url, path=str(path) if isinstance(path, str) and path else (_path_from_raw_url(url) or "")
                )
            else:
                return None
        else:
            return None
    return out
