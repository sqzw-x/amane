"""PlaybackFactory: builtin local + enabled playback plugins."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import structlog
from fastapi import Request
from starlette.responses import Response

from ..net.errors import FailureReason, SourceError
from ..plugins.api import (
    FilePlaybackTarget,
    HlsLocator,
    HlsPlaybackTarget,
    PlaybackProvider,
    PlaybackQuery,
    PluginContext,
    SubtitleTrack,
    UpstreamPlaybackTarget,
)
from ..plugins.models import PluginConfig, SourceCapability
from .cache import PlaybackCaches
from .hls import (
    HLS_CONTENT_TYPE,
    PLAYLIST_CACHE_CONTROL,
    HlsUriMap,
    rewrite_playlist,
    uri_looks_like_playlist,
)
from .href import hls_part_href
from .local import LOCAL_SOURCE_ID, LocalPlaybackProvider
from .proxy import HLS_KEY_CACHE_CONTROL, NOSNIFF, StreamClient

if TYPE_CHECKING:
    from ..crawlers.http import HttpClient
    from ..net.http import WebClient
    from ..plugins.manager import PluginManager

logger = structlog.get_logger()

PROBE_BUDGET_SECONDS = 1.5
SOURCE_ID_MAX_LEN = 128


def _absolute_hls_uri(base_url: str, uri: str) -> str:
    """把清单内的 URI 解析为绝对地址.

    只接受主机可代理的绝对 http(s) 地址. ``urljoin`` / ``urlparse`` 对 ``http://[::1`` 这类
    畸形输入抛 ``ValueError``, 必须转成 ``SourceError``, 否则会作为未处理异常返回 500.
    """
    try:
        absolute = urljoin(base_url, uri)
        parsed = urlparse(absolute)
    except ValueError as exc:
        raise SourceError(FailureReason.NO_USABLE_METADATA, detail="播放列表 URI 不受支持") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceError(FailureReason.NO_USABLE_METADATA, detail="播放列表 URI 不受支持")
    stripped = uri.strip()
    if stripped.casefold().startswith(("http://", "https://")):
        return absolute
    base = urlparse(base_url)
    if (parsed.scheme, parsed.netloc.casefold()) != (base.scheme, base.netloc.casefold()):
        raise SourceError(FailureReason.NO_USABLE_METADATA, detail="播放列表 URI 跨源")
    return absolute


@dataclass(frozen=True, slots=True)
class ListedSource:
    source_id: str
    name: str
    content_type: str
    seekable: bool
    available: bool
    media_file_id: int | None
    detail: str | None = None
    subtitles: tuple[SubtitleTrack, ...] = ()


@dataclass(slots=True)
class PlaybackState:
    """跨 rebuild 存活的进程内播放状态.

    token 表与探测缓存的所有权在 ``AppRuntime`` 上. 若它们随 ``PlaybackFactory`` 每次 rebuild
    一起丢弃, 播放中修改任意热配置都会让在播 HLS 会话的分片 token 失效. 只有插件集合变化
    (安装 / 卸载 / 重载 / 启停) 时才 ``reset()``, 此时旧 token 必须失效.
    """

    hls: HlsUriMap = field(default_factory=HlsUriMap)
    caches: PlaybackCaches = field(default_factory=PlaybackCaches)

    def reset(self) -> None:
        self.hls.reset()
        self.caches.reset()


class PlaybackFactory:
    def __init__(
        self,
        *,
        plugin_manager: PluginManager | None,
        plugin_configs: dict[str, PluginConfig],
        http_client: HttpClient,
        web_client: WebClient,
        data_dir: Path,
        safe_dirs: list[Path] | None,
        proxy: str | None,
        state: PlaybackState | None = None,
    ) -> None:
        self._plugin_manager = plugin_manager
        self._plugin_configs = plugin_configs
        self._http_client = http_client
        self._web_client = web_client
        self._data_dir = data_dir
        self._safe_dirs = safe_dirs
        self._stream = StreamClient(proxy=proxy)
        shared = state if state is not None else PlaybackState()
        self._caches = shared.caches
        self._providers: dict[str, PlaybackProvider] = {}
        self._local = LocalPlaybackProvider(safe_dirs)
        self._hls = shared.hls

    async def aclose(self) -> None:
        await self._stream.aclose()

    @property
    def stream(self) -> StreamClient:
        return self._stream

    @property
    def caches(self) -> PlaybackCaches:
        return self._caches

    def known_source(self, source_id: str) -> bool:
        if len(source_id) > SOURCE_ID_MAX_LEN:
            return False
        if source_id == LOCAL_SOURCE_ID:
            return True
        return self._plugin_manager is not None and self._plugin_manager.has_playback_plugin(source_id)

    def enabled(self, source_id: str) -> bool:
        if source_id == LOCAL_SOURCE_ID:
            return True
        if self._plugin_manager is None or not self._plugin_manager.has_playback_plugin(source_id):
            return False
        return self._plugin_configs.get(source_id, PluginConfig()).enabled

    def provider(self, source_id: str) -> PlaybackProvider | None:
        if not self.enabled(source_id):
            return None
        cached = self._providers.get(source_id)
        if cached is not None:
            return cached
        if source_id == LOCAL_SOURCE_ID:
            self._providers[source_id] = self._local
            return self._local
        if self._plugin_manager is None:
            return None
        config = self._plugin_configs.get(source_id, PluginConfig())
        plugin_dir = self._data_dir / "plugins" / source_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        built = self._plugin_manager.build_playback_provider(
            source_id,
            context=PluginContext(
                source_id=source_id,
                http_client=self._http_client,
                web_client=self._web_client,
                data_dir=plugin_dir,
            ),
            config=config,
        )
        self._providers[source_id] = built
        return built

    def playback_source_ids(self) -> list[str]:
        ids = [LOCAL_SOURCE_ID]
        if self._plugin_manager is None:
            return ids
        ids.extend(
            descriptor.id
            for descriptor in self._plugin_manager.plugin_descriptors()
            if descriptor.supports(SourceCapability.PLAYBACK) and self.enabled(descriptor.id)
        )
        return ids

    async def list_sources(self, query: PlaybackQuery) -> list[ListedSource]:
        ids = self.playback_source_ids()
        tasks = [asyncio.create_task(self._probe_one(source_id, query)) for source_id in ids]
        _done, pending = await asyncio.wait(tasks, timeout=PROBE_BUDGET_SECONDS)
        timed_out = set(pending)
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        results: list[ListedSource] = []
        for task, source_id in zip(tasks, ids, strict=True):
            if task in timed_out:
                results.append(
                    ListedSource(
                        source_id=source_id,
                        name=source_id,
                        content_type="video/mp4",
                        seekable=False,
                        available=False,
                        media_file_id=None,
                        detail="探测超时",
                    )
                )
                continue
            try:
                results.append(task.result())
            except Exception:
                logger.exception("playback probe task failed", source=source_id)
                results.append(
                    ListedSource(
                        source_id=source_id,
                        name=source_id,
                        content_type="video/mp4",
                        seekable=False,
                        available=False,
                        media_file_id=None,
                        detail="探测失败",
                    )
                )
        return results

    async def _probe_one(self, source_id: str, query: PlaybackQuery) -> ListedSource:
        cache_key = f"{source_id}:{query.metadata_id}:{query.selected_file_id}"
        hit = self._caches.probe_hits.get(cache_key)
        if isinstance(hit, ListedSource):
            return hit
        if self._caches.probe_none.is_blocked(cache_key):
            return ListedSource(
                source_id=source_id,
                name=source_id,
                content_type="video/mp4",
                seekable=False,
                available=False,
                media_file_id=None,
            )
        if self._caches.probe_fail.is_blocked(cache_key):
            return ListedSource(
                source_id=source_id,
                name=source_id,
                content_type="video/mp4",
                seekable=False,
                available=False,
                media_file_id=None,
                detail="上游暂时不可用",
            )

        async def _run() -> ListedSource:
            provider = self.provider(source_id)
            if provider is None:
                return ListedSource(
                    source_id=source_id,
                    name=source_id,
                    content_type="video/mp4",
                    seekable=False,
                    available=False,
                    media_file_id=None,
                )
            try:
                offer = await provider.probe(query)
            except SourceError as exc:
                logger.warning("playback probe failed", source=source_id, error=str(exc))
                self._caches.probe_fail.put(cache_key, True)
                return ListedSource(
                    source_id=source_id,
                    name=source_id,
                    content_type="video/mp4",
                    seekable=False,
                    available=False,
                    media_file_id=None,
                    detail="上游失败",
                )
            except Exception:
                logger.exception("playback probe crashed", source=source_id)
                self._caches.probe_fail.put(cache_key, True)
                return ListedSource(
                    source_id=source_id,
                    name=source_id,
                    content_type="video/mp4",
                    seekable=False,
                    available=False,
                    media_file_id=None,
                    detail="探测失败",
                )
            if offer is None:
                self._caches.probe_none.put(cache_key, True)
                return ListedSource(
                    source_id=source_id,
                    name=source_id,
                    content_type="video/mp4",
                    seekable=False,
                    available=False,
                    media_file_id=None,
                )
            listed = ListedSource(
                source_id=source_id,
                name=offer.name,
                content_type=offer.content_type,
                seekable=offer.seekable,
                available=True,
                media_file_id=offer.media_file_id,
                subtitles=offer.subtitles,
            )
            self._caches.probe_hits.put(cache_key, listed)
            return listed

        return await self._caches.coalesce(f"probe:{cache_key}", _run)

    async def resolve(
        self,
        source_id: str,
        query: PlaybackQuery,
    ) -> FilePlaybackTarget | UpstreamPlaybackTarget | HlsPlaybackTarget:
        open_key = f"{source_id}:{query.metadata_id}:{query.selected_file_id}"
        if self._caches.open_fail.is_blocked(open_key):
            raise SourceError(FailureReason.NETWORK, detail="上游暂时不可用")
        provider = self.provider(source_id)
        if provider is None:
            raise LookupError(source_id)
        try:
            target = await provider.resolve(query)
        except SourceError:
            self._caches.open_fail.put(open_key, True)
            raise
        except Exception:
            self._caches.open_fail.put(open_key, True)
            logger.exception("playback resolve crashed", source=source_id)
            raise SourceError(FailureReason.NETWORK, detail="解析播放源失败") from None
        if target is None:
            raise LookupError(source_id)
        if isinstance(target, FilePlaybackTarget):
            if source_id != LOCAL_SOURCE_ID:
                raise SourceError(FailureReason.NO_USABLE_METADATA, detail="插件不得返回本地文件")
            return target
        return target

    def _map_hls_uri(
        self,
        source_id: str,
        query: PlaybackQuery,
        locator: HlsLocator,
        uri: str,
        base_url: str,
        is_key: bool,
    ) -> str:
        token = self._hls.register(
            source_id=source_id,
            query=query,
            locator=locator,
            uri=_absolute_hls_uri(base_url, uri),
            is_key=is_key,
        )
        return hls_part_href(source_id, query.metadata_id, query.selected_file_id, token)

    async def hls_playlist_text(
        self,
        source_id: str,
        query: PlaybackQuery,
        target: HlsPlaybackTarget | None = None,
    ) -> str:
        resolved = target if target is not None else await self.resolve(source_id, query)
        if not isinstance(resolved, HlsPlaybackTarget):
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail="不是 HLS 播放源")
        playlist = await resolved.locator.load_playlist(query)
        return rewrite_playlist(
            playlist.text,
            lambda uri, is_key: self._map_hls_uri(
                source_id,
                query,
                resolved.locator,
                uri,
                playlist.base_url,
                is_key,
            ),
        )

    def playlist_response(self, text: str, *, head: bool) -> Response:
        headers = {**NOSNIFF, "Cache-Control": PLAYLIST_CACHE_CONTROL}
        if head:
            return Response(status_code=200, headers=headers, media_type=HLS_CONTENT_TYPE)
        return Response(
            content=text.encode("utf-8"),
            media_type=HLS_CONTENT_TYPE,
            headers=headers,
        )

    async def serve_hls_part(
        self,
        request: Request,
        *,
        source_id: str,
        metadata_id: int,
        media_file_id: int | None,
        token: str,
    ) -> Response:
        """转发一条清单内 URI.

        只按三个标量核对 token 归属, 不重建查询快照: 分片请求每秒数次, 而定位所需的一切都在
        ``mapped`` 里.
        """
        mapped = self._hls.get(token)
        if (
            mapped is None
            or mapped.source_id != source_id
            or mapped.query.metadata_id != metadata_id
            or mapped.query.selected_file_id != media_file_id
        ):
            raise LookupError(token)
        located = await mapped.locator.locate(mapped.query, mapped.uri)
        base_url = located.url

        def map_uri(uri: str, is_key: bool) -> str:
            return self._map_hls_uri(source_id, mapped.query, mapped.locator, uri, base_url, is_key)

        def rewriter(text: str) -> str:
            return rewrite_playlist(text, map_uri)

        if uri_looks_like_playlist(mapped.uri, located):
            raw = await self._stream.fetch_bytes(source_id=source_id, target=located)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceError(FailureReason.NETWORK, detail="播放列表不是文本") from exc
            return self.playlist_response(rewriter(text), head=request.method == "HEAD")
        return await self._stream.proxy(
            request,
            source_id=source_id,
            target=located,
            allow="hls_part",
            rewrite_playlist=rewriter,
            # 密钥内容会轮换, 不允许写入浏览器不可变缓存.
            cache_control=HLS_KEY_CACHE_CONTROL if mapped.is_key else None,
        )

    async def load_subtitle(
        self,
        source_id: str,
        query: PlaybackQuery,
        track_id: str,
    ) -> str | UpstreamPlaybackTarget:
        provider = self.provider(source_id)
        if provider is None:
            raise LookupError(source_id)
        try:
            result = await provider.subtitle(query, track_id)
        except SourceError:
            raise
        except Exception:
            logger.exception("playback subtitle crashed", source=source_id)
            raise SourceError(FailureReason.NETWORK, detail="读取字幕失败") from None
        if result is None:
            raise LookupError(track_id)
        return result
