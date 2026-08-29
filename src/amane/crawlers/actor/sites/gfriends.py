"""gFriends 头像库 - Filetree 索引按名查图."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from amane.enums import ActorGender, SiteName
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ..base import ActorCrawler
from ..models import ActorMetadata

_DEFAULT_REPO = "https://github.com/gfriends/gfriends"


@dataclass(frozen=True, slots=True)
class GFriendsImage:
    """Filetree 命中: raw 图 URL + 仓库相对路径."""

    url: str
    path: str  # Content/{folder}/{file}


class GFriendsActorCrawler(ActorCrawler):
    """按艺名查 gFriends Filetree 头像 URL."""

    def __init__(self, client: Any, config: Any = None, *, data_dir: Path | None = None, repo_url: str | None = None):
        super().__init__(client, config)
        self._data_dir = data_dir
        self._repo_url = (repo_url or _DEFAULT_REPO).rstrip("/")
        self._index: dict[str, GFriendsImage] | None = None

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
        """按名查 Filetree 索引取头像; 未命中返回 None."""
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

    async def _search(self, name: str) -> str | None:
        """未使用; 本源直接经 fetch() 查索引."""
        raise NotImplementedError

    async def _scrape(self, url: str) -> ActorMetadata | None:
        """未使用; 本源直接经 fetch() 查索引."""
        raise NotImplementedError

    async def _ensure_index(self) -> dict[str, GFriendsImage]:
        """加载索引: 优先读本地缓存, 否则拉取 Filetree.json 并写缓存."""
        if self._index is not None:
            return self._index
        cache = self._cache_path()
        if cache and cache.exists():
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    parsed = _index_from_cache(raw)
                    if parsed is not None:
                        self._index = parsed
                        return self._index
            except OSError, json.JSONDecodeError:
                self.logger.warning("gfriends cache unreadable", path=str(cache))

        tree_url = f"{self._raw_base()}/Filetree.json"
        data = await self.client.get_json(tree_url, cookies=self.cookies)
        if not isinstance(data, dict):
            self._index = {}
            return self._index
        self._index = flatten_filetree(data, raw_base=self._raw_base())
        if cache:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                payload = {k: {"url": v.url, "path": v.path} for k, v in self._index.items()}
                cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            except OSError:
                self.logger.warning("gfriends cache write failed", path=str(cache))
        return self._index

    def _raw_base(self) -> str:
        # https://github.com/gfriends/gfriends → raw .../master
        if "github.com" in self._repo_url and "raw.githubusercontent.com" not in self._repo_url:
            path = self._repo_url.split("github.com/", 1)[-1]
            return f"https://raw.githubusercontent.com/{path}/master"
        return self.base_url

    def _cache_path(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "gfriends.json"


def flatten_filetree(tree: dict[str, Any], *, raw_base: str) -> dict[str, GFriendsImage]:
    """
    扁平化 Filetree.json → filename → raw URL + Content 相对路径.

    Content 子目录按名字母序遍历; 同一文件名先出现者保留 (倾向非 z-* 低质源).
    """
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
            # value 形如 "Name.jpg?t=..." 或指向规范文件名
            target = str(value).split("?", 1)[0] if value else str(filename)
            # value 常为规范/AI-Fix 文件名; 路径 = Content/{folder}/{target}
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
    """GitHub blob 页 (详情 source Badge 可点开)."""
    encoded = "/".join(quote(part, safe="") for part in content_path.split("/"))
    return f"{repo_url.rstrip('/')}/blob/master/{encoded}"


def _path_from_raw_url(url: str) -> str | None:
    """从 raw.githubusercontent URL 还原 Content/... 相对路径."""
    path = urlsplit(url).path
    marker = "/Content/"
    idx = path.find(marker)
    if idx < 0:
        return None
    # path 形如 /owner/repo/master/Content/...
    return path[idx + 1 :]  # drop leading /


def _index_from_cache(raw: dict[str, Any]) -> dict[str, GFriendsImage] | None:
    """兼容旧缓存 (filename→url 字符串) 与新格式 (filename→{url,path})."""
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
