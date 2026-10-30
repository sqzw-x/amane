"""封面与剧照语义检索: 向量入库、内存索引与相似度查询.

向量按 ``(条目, 模型, 图片)`` 存在 sidecar 库 (见 :class:`amane.db.embeddings.EmbeddingStore`),
检索时整表装入内存做一次点积. 封面与剧照共用同一个视觉塔与同一次点积, 结果用 ``kind`` 区分来源;
图片只取已经落盘的 Resource.

索引常驻, 生命周期跟着服务而不是最后一次查询. 写库后代次前进, 旧索引继续服务, 后台单飞重装,
装好再原子换掉; 删除条目则在写入返回前就地清位图, 不必等重装. 一次查询的图片分数与条目排名按
``(查询, 模型, 代次)`` 缓存, 翻页和改筛选只在这份排名上切, 不再点积.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog
from PIL import Image

from ..db.embeddings import (
    EmbeddingIndex,
    EmbeddingKind,
    EmbeddingSnapshotError,
    EmbeddingWrite,
    EmbeddingWriteEvent,
    ModelRef,
)
from .encoder import (
    ImageEncoder,
    SemanticUnavailable,
    TextEncoder,
    artifact_present,
    build_image_encoder,
    build_text_encoder,
)
from .models import (
    DEFAULT_SPEC,
    Readiness,
    backend_for_platform,
    backend_label,
    download_size,
    ensure_model_files,
    model_key,
    model_paths,
    model_root,
    readiness,
    tower_sizes,
)
from .ranking import EntryRanking, best_row, dot, rank_entries

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..config import SemanticConfig
    from ..db import Metadata, Repository, Resource
    from ..db.embeddings import EmbeddingStore, ModelStats
    from ..media import ResourceStore
    from .models import ModelPaths, ModelSpec

logger = structlog.get_logger()

BATCH = 8
"""一批编码的张数; Core ML 视觉塔是 batch=1 包, 这里只摊薄解码与写库开销."""

PAGE = 200
"""遍历 Metadata 的页大小."""

TEXT_IDLE_SECONDS = 600
"""文本塔的空闲释放时间; 内存索引不受影响, 它与文本塔各自常驻."""

QUERY_CACHE_SIZE = 8
"""缓存多少次查询的排名; 每次约 ``图片数 × 4 B`` 加条目数组, 100 万行约 7 MB."""

TEXT_VECTOR_CACHE_SIZE = 32
"""文本向量缓存条数, 每条约 3 KB; 切换索引不清空, 只重做点积."""

RELOAD_QUIET_SECONDS = 5.0
"""普通写入后等这么久再重装, 把一次刮削的零散写入并成一次."""

RELOAD_MAX_WAIT_SECONDS = 30.0
"""持续写入时最多拖这么久, 避免重装被无限推迟."""

BUILD_RELOAD_SECONDS = 600.0
BUILD_RELOAD_GROWTH = 0.2
"""全量索引任务运行中的重装条件: 距上次装载满 10 分钟, 且行数增长 20%.

一次数小时的索引不该每个窗口都重装 3 GB; 任务后段若只改图不增行, 会等任务结束再重装.
"""

RELOAD_RECHECK_SECONDS = 30.0
"""后台等待期间的复查间隔; 上面的条件随时间变化, 不能只靠写入通知唤醒."""

BENCHMARK_COVERS = 4
BENCHMARK_STILLS = 4
"""性能测试各抽多少张封面与剧照."""

BENCHMARK_ENTRY_SCAN = 300
"""找不到足够图片时最多翻多少条目."""

BENCHMARK_ROUNDS = 2
"""图片编码测速轮次; 取总耗时算吞吐, 避免单轮抖动."""

BENCHMARK_QUERIES = (
    "white shirt",
    "蕾丝",
    "海边",
    "two people",
    "红色",
    "office",
    "泳装",
    "夜景",
    "black hair",
    "公园",
)
"""查询测速的固定短语, 中英混合, 长度 1–3 词."""

ProgressCallback = Callable[[int, int, str], Awaitable[None]]


@dataclass(frozen=True)
class SemanticHit:
    metadata_id: int
    kind: EmbeddingKind
    resource_url: str
    score: float


@dataclass(frozen=True)
class SemanticStatus:
    """轻量状态: 只够页面决定是否显示入口与提示, 不读取存储统计."""

    enabled: bool
    available: bool
    """配置允许且检索所需文件就绪, 可以进入语义检索."""
    search_ready: bool
    """文本塔与 tokenizer 就绪, 检索可用."""
    has_embeddings: bool
    """当前模型是否已有任意向量; 索引为空时页面提示先建索引."""
    download_size: int
    """当前后端首次下载的总字节数, 供缺模型提示展示."""


@dataclass(frozen=True)
class SemanticDiagnostics:
    """用户手册的诊断数据: 运行时状态、路径、占用与索引统计."""

    enabled: bool
    backend: str | None
    model: str | None
    model_label: str
    """模型展示名, 大小写按厂商写法."""
    backend_label: str | None
    """推理后端展示名."""
    text_loaded: bool
    """文本塔当前是否在内存中; 闲置超时后释放."""
    vision_loaded: bool
    """视觉塔当前是否在内存中; 索引任务结束后释放 (自动维护打开时常驻)."""
    search_ready: bool
    """文本塔与 tokenizer 就绪, 检索可用."""
    index_ready: bool
    """视觉塔就绪, 索引任务不会触发下载."""
    download_size: int
    """当前后端首次下载的总字节数."""
    vision_size: int
    """视觉塔模型文件解压后的大小."""
    text_size: int
    """文本塔模型文件解压后的大小."""
    model_path: str
    """模型目录; 未下载时也可能存在父目录."""
    model_bytes: int
    """模型目录在本地占用的字节数."""
    embeddings_path: str
    """向量库文件路径."""
    embeddings_bytes: int
    """向量库及其 WAL 文件占用的字节数."""
    indexed_metadata: int
    """当前模型下已建有向量的条目数."""
    indexed_at: str | None
    """当前模型最后一次写入向量的时间; 未建索引时为 None."""
    models: list[ModelStats]


@dataclass(frozen=True)
class IndexStats:
    scanned: int
    indexed: int
    skipped: int
    missing: int
    failed: int


@dataclass(frozen=True)
class BenchmarkStats:
    backend: str
    model: str
    images_per_second: float
    query_ms: float
    load_ms: float
    samples: int
    measured_at: str


@dataclass(frozen=True)
class _Image:
    kind: EmbeddingKind
    url: str
    path: Path
    content_hash: str | None


@dataclass(frozen=True)
class _Snapshot:
    """检索读到的一整套数组; 删条目时就地清 :attr:`alive`, 重装后整体换掉."""

    index: EmbeddingIndex
    alive: np.ndarray
    """``(条目数,)`` bool; 已删除条目在切换索引前靠它屏蔽."""
    loaded_at: float


@dataclass
class _QueryState:
    """一次查询的中间结果; 只存数组, 不持有索引, 切换索引时随缓存整体丢弃."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scores: np.ndarray | None = None
    """``(图片数,)`` float32 点积结果."""
    rankings: dict[EmbeddingKind | None, EntryRanking] = field(default_factory=dict)


def _tree_size(root: Path) -> int:
    """目录在磁盘上占用的字节数; 不存在时返回 0."""
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _file_set_size(path: Path) -> int:
    """主文件与 SQLite 的 -wal / -shm 一起算, 与清理前的实际占用一致."""
    total = 0
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _embed_batch(encoder: ImageEncoder, paths: Sequence[Path]) -> tuple[np.ndarray, list[int]]:
    """在线程里解码并编码一批图片; 单张解码失败只跳过它, 返回保留下来的下标."""
    images: list[Image.Image] = []
    kept: list[int] = []
    try:
        for index, path in enumerate(paths):
            try:
                with Image.open(path) as image:
                    images.append(image.convert("RGB"))
            except Exception:
                # 资源目录里有下载不完整的图 (后缀是 .jpg 但字节不可识别), 跳过而不是拖垮整批.
                logger.warning("embedding image decode failed", path=str(path))
                continue
            kept.append(index)
        if not images:
            return np.zeros((0, encoder.dim), dtype=np.float32), kept
        return encoder.embed_images(images), kept
    finally:
        for image in images:
            image.close()


def _diff(
    existing: dict[str, tuple[EmbeddingKind, str | None]], images: Sequence[_Image], *, force: bool = False
) -> tuple[list[str], list[_Image]]:
    """``(已不在条目里的 url, 需要重算的图)``; 签名是 ``(kind, content_hash)``."""
    urls = {image.url for image in images}
    stale = [url for url in existing if url not in urls]
    changed = [image for image in images if force or existing.get(image.url) != (image.kind, image.content_hash)]
    return stale, changed


def _writes(
    metadata_id: int, images: Sequence[_Image], vectors: np.ndarray, kept: Sequence[int]
) -> list[EmbeddingWrite]:
    # ``kept`` 存批内下标, 向量按存活顺序压缩: 两套下标不能混用.
    return [
        EmbeddingWrite(
            metadata_id=metadata_id,
            kind=images[index].kind,
            resource_url=images[index].url,
            content_hash=images[index].content_hash,
            vector=vectors[position],
        )
        for position, index in enumerate(kept)
    ]


class SemanticService:
    """两个塔分开加载: 检索只用文本塔 (空闲 ``TEXT_IDLE_SECONDS`` 后释放), 索引只用视觉塔.

    模型文件只在 :meth:`build_index` 里下载; 其余入口只认本地已就绪的文件. 每个塔的加载与推理
    各由一把锁串行化: 后端实例不保证线程安全, 索引任务与增量维护可能同时用到视觉塔.

    内存索引常驻: 文本塔空闲释放不再丢掉它, 写入后由后台任务单飞重装 (见 :meth:`_reload_loop`).
    """

    def __init__(self, repo: Repository, store: ResourceStore, data_dir: Path, config: SemanticConfig) -> None:
        self.repo = repo
        self.store = store
        self.data_dir = data_dir
        self.config = config
        self._vision: ImageEncoder | None = None
        self._text: TextEncoder | None = None
        self._vision_lock = asyncio.Lock()
        self._text_lock = asyncio.Lock()
        self._text_idle: asyncio.TimerHandle | None = None
        self._model: ModelRef | None = None
        self._snapshot: _Snapshot | None = None
        self._load_lock = asyncio.Lock()
        self._queries: OrderedDict[tuple[str, int, int], _QueryState] = OrderedDict()
        self._vectors: OrderedDict[str, np.ndarray] = OrderedDict()
        self._reload_task: asyncio.Task[None] | None = None
        self._reload_retry_at = 0.0
        """上次重装失败后的冷却; 读盘持续失败时不至于每个请求都去撞一次."""
        self._dirty_at = 0.0
        self._dirty_since: float | None = None
        self._reload_now = False
        self._building = False
        self._tombstones: list[tuple[int, int]] = []
        """``(产生时的代次, 条目 id)``; 只用于切换索引时给快照里仍未删掉的行补刀."""
        if repo.embeddings is not None:
            repo.embeddings.add_write_listener(self._on_store_write)

    @property
    def embeddings(self) -> EmbeddingStore:
        store = self.repo.embeddings
        if store is None:
            raise SemanticUnavailable("向量库未初始化")
        return store

    @property
    def spec(self) -> ModelSpec:
        return DEFAULT_SPEC

    @property
    def enabled(self) -> bool:
        return True

    @property
    def backend(self) -> str | None:
        return backend_for_platform()

    @property
    def model(self) -> str | None:
        spec = self.spec
        return model_key(spec.name, spec.version)

    @property
    def model_label(self) -> str:
        spec = self.spec
        return f"{spec.display_name} {spec.version}"

    @property
    def search_ready(self) -> bool:
        return self.readiness().search

    @property
    def index_ready(self) -> bool:
        return self.readiness().index

    def readiness(self) -> Readiness:
        return readiness(self.spec, backend_for_platform(), self.data_dir)

    async def _local_paths(self, *, download: bool) -> ModelPaths:
        spec = self.spec
        backend = backend_for_platform()
        if download:
            return await ensure_model_files(spec, backend, self.data_dir)
        return model_paths(spec, backend, self.data_dir)

    async def _load_vision(self, *, download: bool) -> ImageEncoder:
        paths = await self._local_paths(download=download)
        if not artifact_present(paths.vision):
            raise SemanticUnavailable("视觉模型尚未下载, 请先运行一次语义索引任务")
        return await asyncio.to_thread(build_image_encoder, paths)

    async def _load_text(self) -> TextEncoder:
        paths = await self._local_paths(download=False)
        if not (paths.tokenizer.exists() and artifact_present(paths.text)):
            raise SemanticUnavailable("文本模型尚未下载, 请提交一次语义索引任务")
        return await asyncio.to_thread(build_text_encoder, paths)

    async def _embed_images(self, paths: Sequence[Path], *, download: bool) -> tuple[np.ndarray, list[int]]:
        async with self._vision_lock:
            if self._vision is None:
                self._vision = await self._load_vision(download=download)
            return await asyncio.to_thread(_embed_batch, self._vision, paths)

    async def _embed_texts(self, texts: Sequence[str]) -> list[np.ndarray]:
        async with self._text_lock:
            if self._text is None:
                self._text = await self._load_text()
            encoder = self._text
            vectors = await asyncio.to_thread(lambda: [encoder.embed_text(text) for text in texts])
        self._schedule_text_release()
        return vectors

    def _schedule_text_release(self) -> None:
        if self._text_idle is not None:
            self._text_idle.cancel()
        self._text_idle = asyncio.get_running_loop().call_later(TEXT_IDLE_SECONDS, self._release_text)

    def _release_text(self) -> None:
        """空闲到期: 正在推理的调用持有局部引用, 置空不影响它. 内存索引不受影响, 它常驻."""
        self._text_idle = None
        self._text = None

    def release_vision(self) -> None:
        self._vision = None

    def release_index(self) -> None:
        """丢弃内存索引与查询缓存; 下次检索重新装载. 索引默认常驻, 只有显式调用才释放.

        模型行缓存一并丢掉: sidecar 被删掉重建后模型 id 会变, 留着旧 id 只会一直读到空索引.
        """
        self._snapshot = None
        self._model = None
        self._queries.clear()
        self._vectors.clear()
        self._cancel_reload()

    def release(self) -> None:
        """释放索引与两个塔; 下次检索重新装载并加载文本塔. 内存紧张时的唯一手动入口."""
        self.release_index()
        self.release_vision()
        if self._text_idle is not None:
            self._text_idle.cancel()
            self._text_idle = None
        self._text = None

    def close(self) -> None:
        """断开写通知并停掉后台重装; 热重载换服务与进程退出时调用."""
        if self.repo.embeddings is not None:
            self.repo.embeddings.remove_write_listener(self._on_store_write)
        self._cancel_reload()

    def handoff(self, previous: SemanticService) -> None:
        """热重载交接: 复用旧服务已装载的索引与缓存, 避免配置变更后同步重装整份矩阵."""
        snapshot = previous._snapshot
        if snapshot is None or self._snapshot is not None:
            return
        self._snapshot = snapshot
        self._model = previous._model
        self._queries = previous._queries
        self._vectors = previous._vectors
        self._tombstones = previous._tombstones
        previous._snapshot = None
        previous._model = None
        previous._queries = OrderedDict()
        previous._vectors = OrderedDict()
        previous._tombstones = []

    async def _ensure_model(self) -> ModelRef:
        """模型行注册一次即缓存; 配置变化时服务重建, 缓存随之失效."""
        ref = self._model
        if ref is None:
            spec = self.spec
            ref = await self.embeddings.ensure_model(spec.name, spec.version, spec.dim)
            if ref.dim != spec.dim:
                raise SemanticUnavailable(
                    f"模型 {ref.name}-{ref.version} 的向量维度是 {ref.dim}, 与清单 {spec.dim} 不符"
                )
            self._model = ref
        return ref

    async def status(self) -> SemanticStatus:
        """轻量状态: 只做文件存在性与当前模型是否有向量的检查, 不扫模型目录或向量表."""
        spec = self.spec
        enabled = self.enabled
        search_ready = self.search_ready
        has_embeddings = False
        if enabled and search_ready:
            store = self.repo.embeddings
            if store is not None:
                model_id = await store.model_id(spec.name, spec.version)
                if model_id is not None:
                    has_embeddings = await store.has_embeddings(model_id)
        return SemanticStatus(
            enabled=enabled,
            available=enabled and search_ready,
            search_ready=search_ready,
            has_embeddings=has_embeddings,
            download_size=download_size(spec, backend_for_platform()),
        )

    async def diagnostics(self) -> SemanticDiagnostics:
        """手册诊断: 额外统计模型目录、sidecar 与索引; 不要用于页面预检."""
        spec = self.spec
        backend = backend_for_platform()
        model_dir = model_root(self.data_dir, spec)
        models = await self.embeddings.models()
        current = next(
            (item for item in models if item.ref.name == spec.name and item.ref.version == spec.version), None
        )
        model_bytes, embeddings_bytes = await asyncio.gather(
            asyncio.to_thread(_tree_size, model_dir),
            asyncio.to_thread(_file_set_size, self.embeddings.path),
        )
        indexed_metadata, indexed_at = (
            await self.embeddings.index_stats(current.ref.id) if current is not None else (0, None)
        )
        readiness = self.readiness()
        vision_size, text_size = tower_sizes(spec, backend)
        return SemanticDiagnostics(
            enabled=self.enabled,
            backend=backend,
            backend_label=backend_label(backend),
            model=self.model,
            model_label=self.model_label,
            text_loaded=self._text is not None,
            vision_loaded=self._vision is not None,
            search_ready=readiness.search,
            index_ready=readiness.index,
            download_size=download_size(spec, backend),
            vision_size=vision_size,
            text_size=text_size,
            model_path=str(model_dir),
            model_bytes=model_bytes,
            embeddings_path=str(self.embeddings.path),
            embeddings_bytes=embeddings_bytes,
            indexed_metadata=int(indexed_metadata),
            indexed_at=indexed_at,
            models=models,
        )

    async def search(
        self,
        query: str,
        limit: int,
        kind: EmbeddingKind | None = None,
        allowed_ids: Sequence[int] | None = None,
        offset: int = 0,
    ) -> list[SemanticHit]:
        """单次整句编码; ``allowed_ids`` 是外部筛选后的候选条目集, 排名只在该集合内进行.

        文本塔未就绪时抛 :class:`SemanticUnavailable`, 不触发下载也不建向量库文件.
        """
        vector = await self._text_vector(query)
        snapshot = await self._read_snapshot()
        visible = self._visible(snapshot, kind, allowed_ids)
        if visible is None:
            return []
        ranking, scores = await self._ranking(self._query_state(query, snapshot.index), snapshot.index, vector, kind)
        picks = ranking.order[visible[ranking.order]][offset : offset + limit]
        return [self._hit(snapshot.index, scores, ranking, kind, int(slot)) for slot in picks]

    async def count(self, kind: EmbeddingKind | None = None, allowed_ids: Sequence[int] | None = None) -> int:
        """候选集里去重后的条目数; 列表分页要总分, 不编码文本也不排名."""
        snapshot = await self._read_snapshot()
        visible = self._visible(snapshot, kind, allowed_ids)
        return 0 if visible is None else int(np.count_nonzero(visible))

    @staticmethod
    def _visible(
        snapshot: _Snapshot, kind: EmbeddingKind | None, allowed_ids: Sequence[int] | None
    ) -> np.ndarray | None:
        """条目位图: 未删除、有所请求来源、在候选集内. 排名里没有这些条件, 所以改筛选不动缓存."""
        visible = snapshot.alive
        if kind is not None:
            visible = visible & snapshot.index.kind_layout(kind).present
        if allowed_ids is not None:
            mask = snapshot.index.entry_mask(allowed_ids)
            if mask is None:
                return None
            visible = visible & mask
        return visible

    @staticmethod
    def _hit(
        index: EmbeddingIndex, scores: np.ndarray, ranking: EntryRanking, kind: EmbeddingKind | None, slot: int
    ) -> SemanticHit:
        row = best_row(index, scores, kind, slot)
        return SemanticHit(
            metadata_id=int(index.entry_ids[slot]),
            kind=index.kind_at(row),
            resource_url=index.urls[row],
            score=float(ranking.scores[slot]),
        )

    async def _text_vector(self, query: str) -> np.ndarray:
        """先编码再碰向量库: 文本塔缺失时不该顺手建出 sidecar 文件."""
        vector = self._vectors.get(query)
        if vector is None:
            vector = (await self._embed_texts([query]))[0]
            self._vectors[query] = vector
            while len(self._vectors) > TEXT_VECTOR_CACHE_SIZE:
                self._vectors.popitem(last=False)
        else:
            self._vectors.move_to_end(query)
        return vector

    def _query_state(self, query: str, index: EmbeddingIndex) -> _QueryState:
        key = (query, index.model_id, index.generation)
        state = self._queries.get(key)
        if state is None:
            state = _QueryState()
            self._queries[key] = state
            while len(self._queries) > QUERY_CACHE_SIZE:
                self._queries.popitem(last=False)
        else:
            self._queries.move_to_end(key)
        return state

    async def _ranking(
        self, state: _QueryState, index: EmbeddingIndex, vector: np.ndarray, kind: EmbeddingKind | None
    ) -> tuple[EntryRanking, np.ndarray]:
        """同一查询的并发请求在锁里合并: 先到的算点积与排名, 后到的直接复用."""
        async with state.lock:
            ranking = state.rankings.get(kind)
            scores = state.scores
            if scores is None:
                scores = await asyncio.to_thread(dot, index.matrix, vector)
                state.scores = scores
            if ranking is not None:
                return ranking, scores
            ranking = await asyncio.to_thread(rank_entries, index, scores, kind)
            state.rankings[kind] = ranking
            return ranking, scores

    async def _read_snapshot(self) -> _Snapshot:
        """检索读到的索引; 有过期写入时只登记后台重装, 当前请求继续用旧索引."""
        snapshot = self._snapshot
        if snapshot is not None:
            self._reload_if_dirty(snapshot)
            return snapshot
        async with self._load_lock:
            snapshot = self._snapshot
            if snapshot is None:
                ref = await self._ensure_model()
                try:
                    snapshot = self._install(await self.embeddings.load(ref.id, ref.dim))
                except EmbeddingSnapshotError as exc:
                    # 不缓存空快照: 报给请求, 下次再试.
                    raise SemanticUnavailable("语义索引读取失败, 请检查向量库文件") from exc
            return snapshot

    def _install(self, index: EmbeddingIndex) -> _Snapshot:
        """换上新索引: 重建位图, 丢掉按旧代次缓存的排名."""
        alive = np.ones(index.entry_count, dtype=bool)
        self._tombstones = [item for item in self._tombstones if item[0] > index.generation]
        for _, metadata_id in self._tombstones:
            slot = index.entry_slot(metadata_id)
            if slot is not None:
                alive[slot] = False
        snapshot = _Snapshot(index=index, alive=alive, loaded_at=time.monotonic())
        self._snapshot = snapshot
        self._queries.clear()
        self._dirty_since = None
        self._reload_retry_at = 0.0
        return snapshot

    def _on_store_write(self, event: EmbeddingWriteEvent) -> None:
        """写库后的同步回调: 删除立即屏蔽, 其余只登记并唤醒后台重装."""
        now = time.monotonic()
        self._dirty_at = now
        if self._dirty_since is None:
            self._dirty_since = now
        if event.deleted_ids:
            # 冷装载期间也要记: 正在装的快照代次早于这次删除, 装好后要靠墓碑把它摘掉.
            self._tombstones.extend((event.generation, metadata_id) for metadata_id in event.deleted_ids)
            snapshot = self._snapshot
            if snapshot is not None:
                for metadata_id in event.deleted_ids:
                    slot = snapshot.index.entry_slot(metadata_id)
                    if slot is not None:
                        snapshot.alive[slot] = False
        snapshot = self._snapshot
        if snapshot is not None:
            self._reload_if_dirty(snapshot)

    def _reload_if_dirty(self, snapshot: _Snapshot) -> None:
        if self.embeddings.generation <= snapshot.index.generation:
            return
        if time.monotonic() < self._reload_retry_at:
            return
        if self._reload_task is not None and not self._reload_task.done():
            return
        self._reload_task = asyncio.create_task(self._reload_loop())

    def _cancel_reload(self) -> None:
        task = self._reload_task
        self._reload_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _reload_loop(self) -> None:
        """后台单飞重装: 装好再替换, 装的过程中旧索引一直可用."""
        try:
            while True:
                snapshot = self._snapshot
                if snapshot is None or self.embeddings.generation <= snapshot.index.generation:
                    return
                delay = await self._reload_delay(snapshot)
                if delay > 0:
                    await asyncio.sleep(min(delay, RELOAD_RECHECK_SECONDS))
                    continue
                await self._reload_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._reload_retry_at = time.monotonic() + RELOAD_RECHECK_SECONDS
            logger.warning("semantic index reload failed", exc_info=True)
        finally:
            self._reload_task = None

    async def _reload_delay(self, snapshot: _Snapshot) -> float:
        """还要等多久再重装; 返回 0 表示立刻装."""
        now = time.monotonic()
        if self._building:
            # 全量索引任务自己会写很久: 按时间与行数双条件限流, 任务结束再强制补一次.
            waited = now - snapshot.loaded_at
            if waited < BUILD_RELOAD_SECONDS:
                return BUILD_RELOAD_SECONDS - waited
            rows = await self.embeddings.row_count(snapshot.index.model_id)
            if rows < snapshot.index.row_count * (1 + BUILD_RELOAD_GROWTH):
                return RELOAD_RECHECK_SECONDS
            return 0.0
        quiet = self._dirty_at + RELOAD_QUIET_SECONDS
        cap = (self._dirty_since or now) + RELOAD_MAX_WAIT_SECONDS
        due = max(min(quiet, cap), snapshot.loaded_at + RELOAD_QUIET_SECONDS)
        return max(due - now, 0.0)

    async def _reload_once(self) -> None:
        ref = await self._ensure_model()
        async with self._load_lock:
            snapshot = self._snapshot
            if snapshot is not None and snapshot.index.generation >= self.embeddings.generation:
                return
            self._install(await self.embeddings.load(ref.id, ref.dim))

    async def _settle_index(self) -> None:
        """索引任务收尾: 同步补一次重装, 之后的检索看到的就是任务写完的向量."""
        self._building = False
        snapshot = self._snapshot
        if snapshot is None or self.embeddings.generation <= snapshot.index.generation:
            return
        try:
            await self._reload_once()
        except Exception:
            self._reload_retry_at = time.monotonic() + RELOAD_RECHECK_SECONDS
            logger.warning("semantic index reload failed after build", exc_info=True)

    async def build_index(
        self,
        *,
        limit: int | None = None,
        force: bool = False,
        progress: ProgressCallback | None = None,
        kinds: Sequence[EmbeddingKind] | None = None,
    ) -> IndexStats:
        """全量对账: 只算当前模型与请求的图片种类, 逐条目比较 ``(resource_url, content_hash)``.

        唯一会下载模型的入口. 剧照只取已落盘的资源, 不主动下载; 没有封面的条目不建向量.
        未选中的种类既不重算也不删除, 便于先只索引封面、之后再补剧照.
        """
        selected = frozenset(kinds) if kinds is not None else frozenset(EmbeddingKind)
        ref = await self._ensure_model()
        scanned = indexed = skipped = missing = failed = 0
        offset = 0
        pending: list[tuple[int, _Image]] = []
        stale: list[tuple[int, str]] = []
        seen: set[int] = set()

        async def flush() -> None:
            nonlocal indexed, failed
            if pending:
                batch = list(pending)
                pending.clear()
                try:
                    vectors, kept = await self._embed_images([image.path for _, image in batch], download=True)
                except Exception:
                    logger.warning("semantic batch failed", size=len(batch), exc_info=True)
                    failed += len(batch)
                else:
                    failed += len(batch) - len(kept)
                    writes = [
                        EmbeddingWrite(
                            metadata_id=batch[index][0],
                            kind=batch[index][1].kind,
                            resource_url=batch[index][1].url,
                            content_hash=batch[index][1].content_hash,
                            vector=vectors[position],
                        )
                        for position, index in enumerate(kept)
                    ]
                    indexed += await self.embeddings.upsert(ref.id, writes)
            if stale:
                await self.embeddings.delete_keys(ref.id, list(stale))
                stale.clear()

        try:
            # 先加载视觉塔: 下载或加载失败应让任务失败, 而不是逐批计入 failed.
            async with self._vision_lock:
                if self._vision is None:
                    self._vision = await self._load_vision(download=True)
            self._building = True
            existing = await self.embeddings.signatures(ref.id)
            _, total = await self.repo.list_metadata(limit=1)
            while True:
                items, _ = await self.repo.list_metadata(offset=offset, limit=PAGE)
                if not items:
                    break
                offset += len(items)
                for metadata in items:
                    if limit is not None and scanned >= limit:
                        break
                    assert metadata.id is not None
                    scanned += 1
                    seen.add(metadata.id)
                    images = [image for image in await self._resolve_images(metadata) if image.kind in selected]
                    if not images:
                        missing += 1
                    kept = {url: sig for url, sig in existing.get(metadata.id, {}).items() if sig[0] in selected}
                    gone, changed = _diff(kept, images, force=force)
                    skipped += len(images) - len(changed)
                    stale.extend((metadata.id, url) for url in gone)
                    for image in changed:
                        pending.append((metadata.id, image))
                        if len(pending) >= BATCH:
                            await flush()
                if progress is not None:
                    await progress(scanned, total, "编码封面与剧照")
                if limit is not None and scanned >= limit:
                    break
            await flush()
            if limit is None:
                # 条目删除后 sidecar 清理失败留下的孤儿行; 只有完整遍历才能判定.
                orphans = [metadata_id for metadata_id in existing if metadata_id not in seen]
                await self.embeddings.delete_metadata(orphans, model_id=ref.id)
        finally:
            if not self.config.auto_maintain:
                # 手动 / 定时索引算完即释放; auto_maintain 常驻以承接后续增量.
                self.release_vision()
            await self._settle_index()
        if progress is not None:
            await progress(scanned, total, "完成")
        logger.info(
            "semantic index finished",
            model=self.model,
            scanned=scanned,
            indexed=indexed,
            skipped=skipped,
            missing=missing,
            failed=failed,
        )
        return IndexStats(scanned=scanned, indexed=indexed, skipped=skipped, missing=missing, failed=failed)

    async def benchmark(self) -> BenchmarkStats:
        """在本机抽样测编码与查询速度; 不写向量库, 模型未下载时直接失败.

        图片按端到端计时 (解码 + 推理), 与索引任务同一条路径; 查询只测文本编码, 冷加载单独记.
        """
        if not self.search_ready or not self.index_ready:
            raise SemanticUnavailable("模型尚未下载, 请先运行一次语义索引任务")
        images = await self._sample_images()
        if not images:
            raise SemanticUnavailable("需要至少一张已下载的封面或剧照")
        load_ms = await self._warmup_encoders()
        paths = [image.path for image in images]
        await self._embed_images(paths[: min(2, len(paths))], download=False)
        started = time.perf_counter()
        for _ in range(BENCHMARK_ROUNDS):
            await self._embed_images(paths, download=False)
        elapsed = time.perf_counter() - started
        await self._embed_texts([BENCHMARK_QUERIES[0]])
        durations: list[float] = []
        for phrase in BENCHMARK_QUERIES:
            began = time.perf_counter()
            await self._embed_texts([phrase])
            durations.append((time.perf_counter() - began) * 1000)
        if not self.config.auto_maintain:
            self.release_vision()
        self._schedule_text_release()
        return BenchmarkStats(
            backend=backend_label(backend_for_platform()),
            model=self.model_label,
            images_per_second=len(paths) * BENCHMARK_ROUNDS / elapsed if elapsed > 0 else 0.0,
            query_ms=statistics.median(durations),
            load_ms=load_ms,
            samples=len(paths),
            measured_at=datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        )

    async def _warmup_encoders(self) -> float:
        """按需加载两塔并返回加载耗时; 测试前已加载时接近 0."""
        started = time.perf_counter()
        async with self._vision_lock:
            if self._vision is None:
                self._vision = await self._load_vision(download=False)
        async with self._text_lock:
            if self._text is None:
                self._text = await self._load_text()
        return (time.perf_counter() - started) * 1000

    async def _sample_images(self) -> list[_Image]:
        """封面与剧照各抽若干张; 只看已落盘资源, 不下载."""
        covers: list[_Image] = []
        stills: list[_Image] = []
        offset = 0
        while len(covers) < BENCHMARK_COVERS or len(stills) < BENCHMARK_STILLS:
            items, _ = await self.repo.list_metadata(offset=offset, limit=PAGE)
            if not items:
                break
            for metadata in items:
                for image in await self._resolve_images(metadata):
                    if image.kind is EmbeddingKind.COVER and len(covers) < BENCHMARK_COVERS:
                        covers.append(image)
                    elif image.kind is EmbeddingKind.STILL and len(stills) < BENCHMARK_STILLS:
                        stills.append(image)
            offset += len(items)
            if offset >= BENCHMARK_ENTRY_SCAN:
                break
        return covers + stills

    async def metadata_changed(self, metadata_id: int) -> None:
        """元数据写库后的失效入口 (见 ``Repository.set_media_hook``).

        只删 url 或 content_hash 变了的行, 不加载模型; ``auto_maintain`` 打开且模型已下载时
        再补算这些图. 失败只记日志: 刮削 / 编辑不应因向量库写失败而回滚.

        写入走 store 的写通知: 这里不等重装, 检索侧在切换索引前仍用旧向量.
        """
        try:
            if not self.enabled or not self.embeddings.exists:
                return
            items, _ = await self.repo.list_metadata(ids=[metadata_id], limit=1)
            if not items:
                return
            ref = await self._ensure_model()
            images = await self._resolve_images(items[0])
            existing = await self.embeddings.signatures(ref.id, metadata_id)
            current = existing.get(metadata_id, {})
            gone, changed = _diff(current, images)
            outdated = [*gone, *(image.url for image in changed if image.url in current)]
            await self.embeddings.delete_keys(ref.id, [(metadata_id, url) for url in outdated])
            if not changed or not self.config.auto_maintain or not self.index_ready:
                return
            vectors, kept = await self._embed_images([image.path for image in changed], download=False)
            await self.embeddings.upsert(ref.id, _writes(metadata_id, changed, vectors, kept))
        except SemanticUnavailable as exc:
            logger.info("semantic invalidation skipped", metadata_id=metadata_id, error=str(exc))
        except Exception:
            logger.warning("semantic invalidation failed", metadata_id=metadata_id, exc_info=True)

    async def _resolve_images(self, metadata: Metadata) -> list[_Image]:
        """封面优先; 剧照取站点优先级最高且已有落盘图的那个站点 (同 ``ResourceStore.acquire_extrafanart``).

        无封面则整个条目跳过.
        """
        cover = await self._resolve_cover(metadata)
        if cover is None:
            return []
        images = [cover]
        seen = {cover.content_hash or cover.url}
        for urls in metadata.extrafanart_urls.values():
            stills: list[_Image] = []
            for url in urls:
                found = await self._resolve_resource(url)
                if found is None:
                    continue
                resource, path = found
                digest = resource.content_hash or resource.url
                if digest in seen:
                    continue
                seen.add(digest)
                stills.append(_Image(EmbeddingKind.STILL, resource.url, path, resource.content_hash))
            if stills:
                images.extend(stills)
                break
        return images

    async def _resolve_cover(self, metadata: Metadata) -> _Image | None:
        """海报优先, 缩略图兜底; 只认已经落盘的 Resource."""
        for url in (metadata.poster_url, metadata.thumb_url):
            if not url:
                continue
            found = await self._resolve_resource(url)
            if found is not None:
                resource, path = found
                return _Image(EmbeddingKind.COVER, resource.url, path, resource.content_hash)
        return None

    async def _resolve_resource(self, url: str) -> tuple[Resource, Path] | None:
        # 裁剪后的海报可能以 /api/resources/<hash> 形式留在 URL 字段里.
        if url.startswith("/api/resources/"):
            return await self.store.get_by_url_hash(url.rsplit("/", 1)[-1])
        resource = await self.store.get_by_url(url)
        if resource is None:
            return None
        path = self.store.full_path(resource)
        return (resource, path) if path.is_file() else None
