"""PlaybackFactory: builtin local + enabled playback plugins."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..net.errors import FailureReason, SourceError
from ..plugins.api import (
    FilePlaybackTarget,
    HlsPlaybackTarget,
    PlaybackProvider,
    PlaybackQuery,
    PluginContext,
    UpstreamPlaybackTarget,
)
from ..plugins.models import PluginConfig, SourceCapability
from .cache import PlaybackCaches
from .local import LOCAL_SOURCE_ID, LocalPlaybackProvider
from .proxy import StreamClient

if TYPE_CHECKING:
    from ..crawlers.http import HttpClient
    from ..net.http import WebClient
    from ..plugins.manager import PluginManager

logger = structlog.get_logger()

PROBE_BUDGET_SECONDS = 1.5
SOURCE_ID_MAX_LEN = 128


@dataclass(frozen=True, slots=True)
class ListedSource:
    source_id: str
    name: str
    content_type: str
    seekable: bool
    available: bool
    media_file_id: int | None
    detail: str | None = None


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
    ) -> None:
        self._plugin_manager = plugin_manager
        self._plugin_configs = plugin_configs
        self._http_client = http_client
        self._web_client = web_client
        self._data_dir = data_dir
        self._safe_dirs = safe_dirs
        self._stream = StreamClient(proxy=proxy)
        self._caches = PlaybackCaches()
        self._providers: dict[str, PlaybackProvider] = {}
        self._local = LocalPlaybackProvider(safe_dirs)

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
            )
            self._caches.probe_hits.put(cache_key, listed)
            return listed

        return await self._caches.coalesce(f"probe:{cache_key}", _run)

    async def resolve(
        self,
        source_id: str,
        query: PlaybackQuery,
    ) -> FilePlaybackTarget | UpstreamPlaybackTarget:
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
        if isinstance(target, HlsPlaybackTarget):
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail="此播放源尚未支持 HLS")
        if isinstance(target, FilePlaybackTarget):
            if source_id != LOCAL_SOURCE_ID:
                raise SourceError(FailureReason.NO_USABLE_METADATA, detail="插件不得返回本地文件")
            return target
        return target
