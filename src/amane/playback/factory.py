"""PlaybackFactory: enabled playback plugins."""

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
    PlaybackOffer,
    PlaybackProvider,
    PlaybackQuery,
    PlaybackTarget,
    PluginContext,
    SubtitleTrack,
    UpstreamPlaybackTarget,
)
from ..plugins.models import PluginConfig, SourceCapability
from ..utils.threads import in_thread
from .cache import RESOLVE_TTL_MAX_SECONDS, PlaybackCaches
from .hls import (
    HLS_CONTENT_TYPE,
    PLAYLIST_CACHE_CONTROL,
    FailedHlsUri,
    HlsUriMap,
    rewrite_playlist,
    uri_looks_like_playlist,
)
from .href import hls_part_href
from .proxy import HLS_KEY_CACHE_CONTROL, NOSNIFF, StreamClient

if TYPE_CHECKING:
    from ..crawlers.http import HttpClient
    from ..net.http import WebClient
    from ..plugins.manager import PluginManager

logger = structlog.get_logger()

PROBE_BUDGET_SECONDS = 2.0
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


@in_thread
def _file_target_error(candidate: Path, indexed: tuple[str, ...]) -> str | None:
    """核对插件声明的文件目标, 返回给终端用户的中文原因; ``None`` 表示可以打开.

    两侧都执行 ``resolve()``: 索引里的路径与插件回送的地址互为等价形式 (符号链接、``..``、
    重复分隔符) 时视为同一个文件, 因此指向库外的符号链接照常可播 —— 打开的就是索引里的那条
    路径. 比较只针对条目索引, 不检查库根、``safe_dirs`` 或解析目标 (那是文件浏览器与路径模板
    的配置). 条目没有已索引文件时任何路径都不在索引中, 一律拒绝.
    """
    resolved = candidate.resolve()
    if not any(resolved == Path(item).resolve() for item in indexed):
        return "插件返回的文件不在该条目的索引中"
    if not resolved.is_file():
        return "该条目索引的文件不存在"
    return None


@dataclass(frozen=True, slots=True)
class ListedSource:
    """列表里的一行 = 一条流 (或一条不可用记录).

    ``key`` 为 ``None`` 表示这一行没有可播的流: 来源整个不可用. ``name`` 是主机拼好的展示名
    (来源名 · 流的展示名), 前端直接渲染.
    """

    source_id: str
    key: str | None
    name: str
    content_type: str
    seekable: bool
    available: bool
    detail: str | None = None
    subtitles: tuple[SubtitleTrack, ...] = ()


@dataclass(slots=True)
class PlaybackState:
    """跨 rebuild 存活的进程内播放状态.

    token 表与探测缓存的所有权在 ``AppRuntime`` 上. 若它们随 ``PlaybackFactory`` 每次 rebuild
    一起丢弃, 播放中修改任意热配置都会让在播 HLS 会话的分片 token 失效. 只有插件集合变化
    (安装 / 卸载 / 重载 / 启停) 时才 ``reset()``, 此时旧 token 必须失效.

    解析结果缓存 (``caches.resolve_hits``) 的失效时机不同: 它每次 rebuild 都清空. 配置改动可能
    更换凭据与签名参数, 上一份配置解析出的目标不再可信; token 表跨 rebuild 存活是为了不打断
    在播会话. 两者的失效时机相反, 不要合并.
    """

    hls: HlsUriMap = field(default_factory=HlsUriMap)
    caches: PlaybackCaches = field(default_factory=PlaybackCaches)

    def reset(self) -> None:
        self.hls.reset()
        self.caches.reset()

    def invalidate_resolved(self) -> None:
        """丢弃已解析的播放目标; 每次 rebuild 调用. token 表与探测缓存不受影响."""
        self.caches.resolve_hits.clear()


class PlaybackFactory:
    def __init__(
        self,
        *,
        plugin_manager: PluginManager | None,
        plugin_configs: dict[str, PluginConfig],
        http_client: HttpClient,
        web_client: WebClient,
        data_dir: Path,
        proxy: str | None,
        state: PlaybackState | None = None,
    ) -> None:
        self._plugin_manager = plugin_manager
        self._plugin_configs = plugin_configs
        self._http_client = http_client
        self._web_client = web_client
        self._data_dir = data_dir
        self._stream = StreamClient(proxy=proxy)
        shared = state if state is not None else PlaybackState()
        # 每个 Factory 实例对应一份热配置快照: 配置改动可能更换凭据, 上一份配置解析出的目标
        # 必须失效. token 表与探测缓存不在这里清 (见 PlaybackState).
        shared.invalidate_resolved()
        self._caches = shared.caches
        self._providers: dict[str, PlaybackProvider] = {}
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
        return self._plugin_manager is not None and self._plugin_manager.has_playback_plugin(source_id)

    def enabled(self, source_id: str) -> bool:
        if self._plugin_manager is None or not self._plugin_manager.has_playback_plugin(source_id):
            return False
        return self._plugin_configs.get(source_id, PluginConfig()).enabled

    def provider(self, source_id: str) -> PlaybackProvider | None:
        """构造并缓存来源的 provider.

        ``None`` 表示该来源当前不可用 (未安装或未启用), 调用点按 404 处理. 构造期异常来自插件
        侧 (``build_playback`` 抛错、运行数据目录创建失败等), 必须在这里归为 ``SourceError``,
        否则会作为未处理异常从路由返回 500. ``LookupError`` 例外: 插件管理器认不出该来源等同于
        「未安装」, 仍沿 404 语义向上传递.
        """
        if not self.enabled(source_id):
            return None
        cached = self._providers.get(source_id)
        if cached is not None:
            return cached
        if self._plugin_manager is None:
            return None
        config = self._plugin_configs.get(source_id, PluginConfig())
        plugin_dir = self._data_dir / "plugins" / source_id
        try:
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
        except LookupError:
            raise
        except Exception as exc:
            logger.exception("playback provider build failed", source=source_id)
            raise SourceError(FailureReason.NETWORK, detail="构建播放源失败") from exc
        self._providers[source_id] = built
        return built

    def playback_source_ids(self) -> list[str]:
        if self._plugin_manager is None:
            return []
        return [
            descriptor.id
            for descriptor in self._plugin_manager.plugin_descriptors()
            if descriptor.supports(SourceCapability.PLAYBACK) and self.enabled(descriptor.id)
        ]

    def _listed_name(self, source_id: str) -> str:
        """来源在列表里的显示名: 插件声明过就用 descriptor 的名字, 否则退回来源 ID.

        探测失败时没有 offer 可取名字, 退回 ID 会让用户看到内部标识 (``sqzw.local``).
        """
        descriptor = self._plugin_manager.descriptor(source_id) if self._plugin_manager is not None else None
        return descriptor.name if descriptor is not None else source_id

    def _unavailable(self, source_id: str, *, detail: str | None = None) -> ListedSource:
        """构造一条不可用记录: 来源整个没有可播的流, 因此没有 key.

        ``detail`` 是给终端用户的原因, 缺省表示无从解释.
        """
        return ListedSource(
            source_id=source_id,
            key=None,
            name=self._listed_name(source_id),
            content_type="video/mp4",
            seekable=False,
            available=False,
            detail=detail,
        )

    def _listed(self, source_id: str, offer: PlaybackOffer) -> ListedSource:
        """把一条流映射成列表行: 行名由主机拼成「来源名 · 流的展示名」.

        ``available`` 由 ``unavailable`` 派生: 分开两个字段会多出「不可播但没原因」与「可播却带
        原因」两种无意义组合, 前者会让整块播放区消失.
        """
        return ListedSource(
            source_id=source_id,
            key=offer.key,
            name=f"{self._listed_name(source_id)} · {offer.name}",
            content_type=offer.content_type,
            seekable=offer.seekable,
            available=offer.unavailable is None,
            detail=offer.unavailable,
            subtitles=offer.subtitles,
        )

    def _listed_all(self, source_id: str, offers: tuple[PlaybackOffer, ...]) -> list[ListedSource]:
        """把一次探测的结果列成行, 丢弃重复的 key.

        重复 key 会让两条流共用同一个地址与缓存桶, 前端的下拉里也会出现两个同值选项. 这是插件
        侧的缺陷, 记日志并保留第一条, 不因此让整个来源消失.
        """
        listed: list[ListedSource] = []
        seen: set[str] = set()
        for offer in offers:
            if offer.key in seen:
                logger.warning("playback duplicate stream key", source=source_id, key=offer.key)
                continue
            seen.add(offer.key)
            listed.append(self._listed(source_id, offer))
        return listed

    async def list_sources(self, query: PlaybackQuery) -> list[ListedSource]:
        """列出全部启用来源的流; 每个来源贡献 1..N 行, 顺序即来源顺序与插件给的流顺序."""
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
                results.append(self._unavailable(source_id, detail="探测超时"))
                continue
            try:
                results.extend(task.result())
            except Exception:
                logger.exception("playback probe task failed", source=source_id)
                results.append(self._unavailable(source_id, detail="探测失败"))
        return results

    async def _probe_one(self, source_id: str, query: PlaybackQuery) -> list[ListedSource]:
        cache_key = f"{source_id}\0{query.metadata_id}"
        hit = self._caches.probe_hits.get(cache_key)
        if isinstance(hit, list):
            return hit
        denied = self._caches.probe_none.get(cache_key)
        if isinstance(denied, ListedSource):
            return [denied]
        if self._caches.probe_fail.is_blocked(cache_key):
            return [self._unavailable(source_id, detail="上游暂时不可用")]

        async def _run() -> list[ListedSource]:
            provider = self.provider(source_id)
            if provider is None:
                return [self._unavailable(source_id)]
            try:
                offers = await provider.probe(query)
            except SourceError as exc:
                if exc.reason is FailureReason.NO_USABLE_METADATA:
                    # 「本条目在此来源没有可播流」不是上游故障: 原因原样给用户, 走空结果那条负缓存.
                    listed = self._unavailable(source_id, detail=exc.detail or "没有可播放的流")
                    self._caches.probe_none.put(cache_key, listed)
                    return [listed]
                logger.warning("playback probe failed", source=source_id, error=str(exc))
                self._caches.probe_fail.put(cache_key, True)
                # 插件侧的探测超时与宿主的预算超时给用户看同一句话: 插件的 detail 可能带上游信息,
                # 因此按插件给出的 reason 决定文案, 不直接展示插件文本.
                detail = "探测超时" if exc.reason is FailureReason.TIMEOUT else "上游失败"
                return [self._unavailable(source_id, detail=detail)]
            except Exception:
                logger.exception("playback probe crashed", source=source_id)
                self._caches.probe_fail.put(cache_key, True)
                return [self._unavailable(source_id, detail="探测失败")]
            listed = self._listed_all(source_id, offers)
            if not listed:
                negative = self._unavailable(source_id)
                self._caches.probe_none.put(cache_key, negative)
                return [negative]
            self._caches.probe_hits.put(cache_key, listed)
            return listed

        return await self._caches.coalesce(f"probe:{cache_key}", _run)

    async def resolve(
        self,
        source_id: str,
        query: PlaybackQuery,
    ) -> PlaybackTarget:
        """解析出由主机执行的播放目标, 复用插件声明的有效期.

        命中缓存的前提是插件在上一次结果里声明了 ``cache_ttl``; 未声明时每次都调用插件的
        ``resolve``. 只有成功的解析结果进缓存: 抛 ``SourceError`` 与返回 ``None`` 仍走
        ``open_fail`` 负缓存. 过长的声明由宿主上限 ``RESOLVE_TTL_MAX_SECONDS`` 截断.
        """
        # key 由插件自选, ``"None"`` 是合法取值: 直接插值会让「没有选中」与「选中了该 key」
        # 共用一份缓存. 插入分隔符并把缺省落成空串, 字符串 key 无 ``\0``.
        open_key = f"{source_id}\0{query.metadata_id}\0{query.selected_key or ''}"
        cached = self._caches.resolve_hits.get(open_key)
        if cached is not None:
            return cached
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
            # 磁盘核对进入线程池: 库根位于网盘挂载时, 事件循环上的 stat 会阻塞其它来源的探测.
            detail = await _file_target_error(target.path, tuple(item.path for item in query.files))
            if detail is not None:
                logger.warning("playback file target rejected", source=source_id, detail=detail)
                raise SourceError(FailureReason.NO_USABLE_METADATA, detail=detail)
        ttl = target.cache_ttl
        if ttl is not None:
            self._caches.resolve_hits.put(open_key, target, ttl=min(ttl, RESOLVE_TTL_MAX_SECONDS))
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
        """把一条清单 URI 登记成 token.

        无法定位的 URI 只作废自己: 清单其余部分照常可播, 浏览器请求它时返回原始失败原因. 密钥
        例外 —— 缺密钥整份清单都播不了, 在这里失败比让浏览器取得清单后逐个分片收到 502 更利于
        排查. ``SourceError.detail`` 会展示给终端用户, 不允许带上游地址.
        """
        try:
            absolute = _absolute_hls_uri(base_url, uri)
        except SourceError as exc:
            if is_key:
                raise
            token = self._hls.register_failed(
                source_id=source_id,
                query=query,
                uri=uri,
                detail=exc.detail,
            )
        else:
            token = self._hls.register(
                source_id=source_id,
                query=query,
                locator=locator,
                uri=absolute,
                is_key=is_key,
            )
        return hls_part_href(source_id, query.metadata_id, query.selected_key, token)

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
        selected_key: str | None,
        token: str,
    ) -> Response:
        """转发一条清单内 URI.

        只按三个标量核对 token 归属, 不重建查询快照: 分片请求每秒数次, 而定位所需的一切都在
        ``mapped`` 里. 归属校验先于失败 token 的 502: 其它来源或其它条目请求同一个 token 一律 404.
        """
        mapped = self._hls.get(token)
        if (
            mapped is None
            or mapped.source_id != source_id
            or mapped.query.metadata_id != metadata_id
            or mapped.query.selected_key != selected_key
        ):
            raise LookupError(token)
        if isinstance(mapped, FailedHlsUri):
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail=mapped.detail)
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
