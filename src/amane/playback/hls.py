"""Rewrite HLS playlists onto host paths. Map playlist URIs to opaque tokens."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from ..plugins.api import HlsLocator, PlaybackQuery, UpstreamPlaybackTarget
from .proxy import is_playlist_type

MAX_HLS_TOKENS = 8192
HLS_CONTENT_TYPE = "application/vnd.apple.mpegurl"
PLAYLIST_CACHE_CONTROL = "private, no-cache"
# 失败 token 的兜底原因: 上游未给出 ``detail`` 时, 502 响应体仍必须带一句可展示的中文原因.
HLS_URI_FAILURE_DETAIL = "播放列表 URI 不可用"

_URI_ATTR = re.compile(r'(URI=)(["\'])([^"\']*)\2', re.IGNORECASE)
_HLS_TYPES = frozenset({"application/vnd.apple.mpegurl", "application/x-mpegurl"})


def is_hls_content_type(content_type: str) -> bool:
    lowered = content_type.casefold().split(";", 1)[0].strip()
    return lowered in _HLS_TYPES or "mpegurl" in lowered


def should_map_uri(uri: str) -> bool:
    """判断清单内的一条 URI 是否需要改写为本机路径.

    只跳过空值与非定位 scheme. 不允许按路径前缀放行: 上游正文里出现的 ``/api/playback/...``
    会因此绕过改写, 浏览器改为请求主机的播放路由, 把上游内容与主机端点接通.
    """
    stripped = uri.strip()
    return bool(stripped) and not stripped.startswith(("data:", "urn:", "#"))


def rewrite_playlist(text: str, map_uri: Callable[[str, bool], str]) -> str:
    """Replace playlist URIs with host paths. Independent URI lines and ``URI=`` attributes.

    ``map_uri`` 的第二个参数表示该 URI 是否来自密钥标签 (``#EXT-X-KEY`` /
    ``#EXT-X-SESSION-KEY``): 密钥按 URI 复用但内容会轮换, 缓存策略必须与媒体分片区分.
    """
    body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    out: list[str] = []
    expect_uri = False
    for line in lines:
        stripped = line.strip()
        if expect_uri:
            if not stripped:
                out.append(line)
                continue
            if stripped.startswith("#"):
                out.append(_rewrite_uri_attrs(line, map_uri) if "URI=" in stripped.upper() else line)
                continue
            expect_uri = False
            out.append(map_uri(stripped, False) if should_map_uri(stripped) else stripped)
            continue
        upper = stripped.upper()
        if upper.startswith(("#EXTINF", "#EXT-X-STREAM-INF")):
            expect_uri = True
            out.append(_rewrite_uri_attrs(line, map_uri) if "URI=" in upper else line)
            continue
        if stripped.startswith("#") and "URI=" in upper:
            out.append(_rewrite_uri_attrs(line, map_uri))
            continue
        out.append(line)
    return "\n".join(out)


def _rewrite_uri_attrs(line: str, map_uri: Callable[[str, bool], str]) -> str:
    is_key = line.strip().upper().startswith(("#EXT-X-KEY", "#EXT-X-SESSION-KEY"))

    def repl(match: re.Match[str]) -> str:
        uri = match.group(3)
        if not should_map_uri(uri):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{map_uri(uri, is_key)}{match.group(2)}"

    return _URI_ATTR.sub(repl, line)


def uri_looks_like_playlist(uri: str, target: UpstreamPlaybackTarget) -> bool:
    if target.content_type is not None and is_playlist_type(target.content_type):
        return True
    for candidate in (uri, target.url):
        path = urlparse(candidate).path.casefold()
        if path.endswith((".m3u8", ".m3u")):
            return True
    return False


@dataclass(frozen=True, slots=True)
class MappedHlsUri:
    uri: str
    locator: HlsLocator
    query: PlaybackQuery
    source_id: str
    is_key: bool


@dataclass(frozen=True, slots=True)
class FailedHlsUri:
    """一条无法定位的清单 URI: 请求它必定失败, 不必再访问上游.

    一条无法定位的 URI 只作废自己, 清单其余部分照常可播; 因此仍然登记 token, 并保留来源与条目
    供归属校验比对. 不保留 URI: 它没有可定位的地址, 失败原因也不允许带上游地址.
    """

    detail: str
    query: PlaybackQuery
    source_id: str


HlsEntry = MappedHlsUri | FailedHlsUri


def _token_for(source_id: str, query: PlaybackQuery, uri: str) -> str:
    payload = f"{source_id}\0{query.metadata_id}\0{query.selected_file_id}\0{uri}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


class HlsUriMap:
    """Process-local token table.

    跨 rebuild 存活 (所有权在 ``PlaybackState``), 只在插件集合变化时 ``reset()``: 播放中改任意
    热配置不应让在播会话的 token 全部失效.
    """

    def __init__(self) -> None:
        self._items: OrderedDict[str, HlsEntry] = OrderedDict()

    def reset(self) -> None:
        self._items.clear()

    def register(
        self,
        *,
        source_id: str,
        query: PlaybackQuery,
        locator: HlsLocator,
        uri: str,
        is_key: bool,
    ) -> str:
        entry = MappedHlsUri(
            uri=uri,
            locator=locator,
            query=query,
            source_id=source_id,
            is_key=is_key,
        )
        return self._store(_token_for(source_id, query, uri), entry)

    def register_failed(
        self,
        *,
        source_id: str,
        query: PlaybackQuery,
        uri: str,
        detail: str | None,
    ) -> str:
        """登记一条无法定位的 URI. ``uri`` 只用于派生 token, 不参与定位.

        同一个 URI 每次得到同一个 token, 因此浏览器手里的清单始终指向同一个必定失败的地址.
        """
        entry = FailedHlsUri(
            detail=detail or HLS_URI_FAILURE_DETAIL,
            query=query,
            source_id=source_id,
        )
        return self._store(_token_for(source_id, query, uri), entry)

    def get(self, token: str) -> HlsEntry | None:
        item = self._items.get(token)
        if item is not None:
            # 被请求的 token 仍在会话中使用, 刷新顺序以免被新注册的 token 挤出上限.
            self._items.move_to_end(token)
        return item

    def _store(self, token: str, entry: HlsEntry) -> str:
        self._items[token] = entry
        self._items.move_to_end(token)
        while len(self._items) > MAX_HLS_TOKENS:
            self._items.popitem(last=False)
        return token
