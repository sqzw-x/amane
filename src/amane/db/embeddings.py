"""封面与剧照向量 sidecar: ``{data_dir}/embeddings.db``.

向量是派生数据, 可随时重建: 删除该文件即关闭语义索引, 下次用到时自动重建. 独立 SQLite,
不经 Alembic; 库内没有跨库外键, 条目删除后由 :class:`Repository` 在主库提交之后清理.

检索时整表装入内存做一次点积, 所以只取点积要用的列. 装载用独立的只读连接与只读事务: 快照内的
行数与行内容自洽, 也不与写入共用 aiosqlite 连接. ``vector`` 为 L2 归一化的 float32 小端字节串.

``generation`` 是进程内的写入代次, 只增不减; 提交成功后同步通知监听者, 检索侧据此判断内存索引
是否过期. 它不落库: 进程重启后代次归零, 内存索引同样不存在, 两者重新对齐.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import numpy as np
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

logger = structlog.get_logger()

BATCH = 1000
"""装载时每次读取的行数."""

DELETE_CHUNK = 500
"""按条目删除时每条语句绑定的 id 数, 给 SQLite 绑定变量上限留余量."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_models (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    version    TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (name, version)
);
CREATE TABLE IF NOT EXISTS media_embeddings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_id  INTEGER NOT NULL,
    model_id     INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    resource_url TEXT NOT NULL,
    content_hash TEXT,
    vector       BLOB NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (metadata_id, model_id, resource_url)
);
CREATE INDEX IF NOT EXISTS ix_media_embeddings_metadata ON media_embeddings (metadata_id);
CREATE INDEX IF NOT EXISTS ix_media_embeddings_model ON media_embeddings (model_id);
"""


class EmbeddingKind(StrEnum):
    COVER = "cover"
    STILL = "still"


_KINDS: tuple[EmbeddingKind, ...] = tuple(EmbeddingKind)
"""内存索引里 ``kind`` 按此顺序存 uint8 码; 库内仍存字符串."""
_KIND_CODE = {kind: code for code, kind in enumerate(_KINDS)}

Signatures = dict[int, dict[str, tuple[EmbeddingKind, str | None]]]
"""``metadata_id → resource_url → (kind, content_hash)``."""


class EmbeddingSnapshotError(RuntimeError):
    """sidecar 存在但读不出来 (磁盘或锁问题).

    调用方要保留手上已有的索引: 把读失败当成空库会让检索静默失效, 直到下一次写入才恢复.
    """


@dataclass(frozen=True)
class EmbeddingWrite:
    """一条待写入的向量; 同 ``(metadata_id, model_id, resource_url)`` 覆盖."""

    metadata_id: int
    kind: EmbeddingKind
    resource_url: str
    content_hash: str | None
    vector: np.ndarray


@dataclass(frozen=True)
class EmbeddingWriteEvent:
    """一次提交成功的写入.

    ``deleted_ids`` 只在整条删除 (``delete_metadata`` 不带模型) 时非空: 检索侧要在删除请求返回前
    把这些条目从结果里摘掉, 而不必等索引重装.
    """

    generation: int
    deleted_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ModelRef:
    id: int
    name: str
    version: str
    dim: int


@dataclass(frozen=True)
class ModelStats:
    ref: ModelRef
    covers: int
    stills: int

    @property
    def count(self) -> int:
        return self.covers + self.stills


@dataclass(frozen=True)
class KindLayout:
    """某一来源的行, 按条目分组; 检索时只对这些行做点积聚合.

    行按 ``(条目 id, 来源)`` 排序后, 同一条目的同一来源是连续的一段, 所以分段起点即可定位.
    """

    rows: np.ndarray
    """``(R,)`` int32, 原行号."""
    offsets: np.ndarray
    """``(E+1,)`` int32, ``rows`` 中每个有条目的分段起止."""
    entries: np.ndarray
    """``(E,)`` int32, 分段对应的条目槽位, 升序."""
    present: np.ndarray
    """``(条目数,)`` bool, 该条目是否有此来源的图."""


@dataclass(frozen=True)
class EmbeddingIndex:
    """一次装载的结果; 检索对 ``matrix`` 做点积, 命中项按条目槽位回查其余数组.

    装载后按 ``(metadata_id, kind)`` 重排一次下标得到 :attr:`row_order`; 矩阵本身不动, 聚合时只
    gather 分数向量. 不要在 SQL 里 ``ORDER BY``: 实测会建临时 B 树, 比装载后再排慢一个量级.
    """

    model_id: int
    dim: int
    generation: int
    matrix: np.ndarray = field(repr=False)
    """``(行数, dim)`` 的 float32 矩阵."""
    metadata_ids: np.ndarray = field(repr=False)
    """``(行数,)`` int64."""
    kinds: np.ndarray = field(repr=False)
    """``(行数,)`` uint8 码; 过滤用 :meth:`kind_layout`, 取值用 :meth:`kind_at`."""
    urls: tuple[str, ...] = field(repr=False)
    row_order: np.ndarray = field(init=False, repr=False)
    """``(行数,)`` int32, 行按 ``(条目 id, 来源)`` 排序后的原行号."""
    entry_ids: np.ndarray = field(init=False, repr=False)
    """``(条目数,)`` int64, 条目 id 升序."""
    entry_offsets: np.ndarray = field(init=False, repr=False)
    """``(条目数+1,)`` int32, 每个条目在 :attr:`row_order` 里的起止."""
    layouts: dict[EmbeddingKind, KindLayout] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        row_order = np.lexsort((self.kinds, self.metadata_ids)).astype(np.int32)
        sorted_ids = self.metadata_ids[row_order]
        if sorted_ids.size:
            boundary = np.empty(sorted_ids.size, dtype=bool)
            boundary[0] = True
            np.not_equal(sorted_ids[1:], sorted_ids[:-1], out=boundary[1:])
            starts = np.flatnonzero(boundary).astype(np.int64)
        else:
            starts = np.zeros(0, dtype=np.int64)
        entry_offsets = np.concatenate((starts, np.asarray([sorted_ids.size], dtype=np.int64))).astype(np.int32)
        sorted_kinds = self.kinds[row_order]
        object.__setattr__(self, "row_order", row_order)
        object.__setattr__(self, "entry_ids", sorted_ids[starts])
        object.__setattr__(self, "entry_offsets", entry_offsets)
        object.__setattr__(
            self,
            "layouts",
            {kind: _kind_layout(kind, sorted_kinds, row_order, starts, len(starts)) for kind in _KINDS},
        )

    @property
    def row_count(self) -> int:
        return int(self.metadata_ids.size)

    @property
    def entry_count(self) -> int:
        return int(self.entry_ids.size)

    def kind_layout(self, kind: EmbeddingKind) -> KindLayout:
        return self.layouts[kind]

    def entry_slot(self, metadata_id: int) -> int | None:
        position = int(np.searchsorted(self.entry_ids, metadata_id))
        if position < self.entry_count and int(self.entry_ids[position]) == metadata_id:
            return position
        return None

    def entry_mask(self, metadata_ids: Collection[int]) -> np.ndarray | None:
        """候选条目在位图上标出; 一个都不在索引里时返回 ``None``."""
        ids = np.fromiter(metadata_ids, dtype=np.int64)
        if ids.size == 0 or self.entry_count == 0:
            return None
        position = np.searchsorted(self.entry_ids, ids)
        known = position < self.entry_count
        position = position[known]
        hits = self.entry_ids[position] == ids[known]
        if not hits.any():
            return None
        mask = np.zeros(self.entry_count, dtype=bool)
        mask[position[hits]] = True
        return mask

    def kind_at(self, position: int) -> EmbeddingKind:
        return _KINDS[int(self.kinds[position])]


def _kind_layout(
    kind: EmbeddingKind, sorted_kinds: np.ndarray, row_order: np.ndarray, starts: np.ndarray, entry_count: int
) -> KindLayout:
    if entry_count == 0:
        return KindLayout(
            rows=np.zeros(0, dtype=np.int32),
            offsets=np.zeros(1, dtype=np.int32),
            entries=np.zeros(0, dtype=np.int32),
            present=np.zeros(0, dtype=bool),
        )
    mask = sorted_kinds == _KIND_CODE[kind]
    counts = np.add.reduceat(mask.astype(np.int32), starts)
    entries = np.flatnonzero(counts)
    offsets = np.zeros(entries.size + 1, dtype=np.int32)
    np.cumsum(counts[entries], out=offsets[1:])
    present = np.zeros(entry_count, dtype=bool)
    present[entries] = True
    return KindLayout(
        rows=row_order[mask].astype(np.int32),
        offsets=offsets,
        entries=entries.astype(np.int32),
        present=present,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _as_bytes(vector: np.ndarray) -> bytes:
    return np.ascontiguousarray(vector, dtype=np.float32).tobytes()


class EmbeddingStore:
    """单连接 (aiosqlite 内部串行化) 的向量库; 首次读写时才建库建表.

    ``generation`` 与写通知只覆盖经本实例的写入. 外部直接改 sidecar、多实例共享 ``data_dir`` 不受
    支持: 那些写入不通知检索侧, 需重启或显式释放索引.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._generation = 0
        self._listeners: list[Callable[[EmbeddingWriteEvent], None]] = []

    @property
    def path(self) -> Path:
        return self._db_path

    @property
    def exists(self) -> bool:
        return self._db_path.is_file()

    @property
    def generation(self) -> int:
        """写到本实例的提交次数, 只增不减."""
        return self._generation

    def add_write_listener(self, listener: Callable[[EmbeddingWriteEvent], None]) -> None:
        """监听器在写入提交之后、事件循环里同步调用; 只能做轻量登记, 不能 await."""
        self._listeners.append(listener)

    def remove_write_listener(self, listener: Callable[[EmbeddingWriteEvent], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self, deleted_ids: Sequence[int] = ()) -> None:
        self._generation += 1
        event = EmbeddingWriteEvent(self._generation, tuple(deleted_ids))
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                # 写入已经提交: 监听者的毛病不能变成写入方的失败, 也不能挡住后面的人.
                logger.warning("embedding write listener failed", exc_info=True)

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is None:
            async with self._lock:
                if self._conn is None:
                    self._db_path.parent.mkdir(parents=True, exist_ok=True)
                    conn = await aiosqlite.connect(self._db_path)
                    await conn.executescript(
                        "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=30000;"
                    )
                    await conn.executescript(_SCHEMA)
                    await conn.commit()
                    self._conn = conn
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def ensure_model(self, name: str, version: str, dim: int) -> ModelRef:
        """不存在则注册; 已存在时返回原行 (``dim`` 不覆盖, 由调用方校验)."""
        conn = await self._ensure()
        await conn.execute(
            "INSERT OR IGNORE INTO semantic_models (name, version, dim, created_at) VALUES (?, ?, ?, ?)",
            (name, version, dim, _now()),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT id, name, version, dim FROM semantic_models WHERE name = ? AND version = ?", (name, version)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError(f"语义模型注册失败: {name} {version}")
        return ModelRef(id=int(row[0]), name=str(row[1]), version=str(row[2]), dim=int(row[3]))

    async def models(self) -> list[ModelStats]:
        """库文件不存在时返回空表, 不为状态查询创建文件."""
        if not self.exists:
            return []
        conn = await self._ensure()
        async with conn.execute(
            "SELECT m.id, m.name, m.version, m.dim,"
            " SUM(CASE WHEN e.kind = ? THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN e.kind = ? THEN 1 ELSE 0 END)"
            " FROM semantic_models m LEFT JOIN media_embeddings e ON e.model_id = m.id"
            " GROUP BY m.id ORDER BY m.id",
            (EmbeddingKind.COVER, EmbeddingKind.STILL),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ModelStats(
                ref=ModelRef(id=int(row[0]), name=str(row[1]), version=str(row[2]), dim=int(row[3])),
                covers=int(row[4] or 0),
                stills=int(row[5] or 0),
            )
            for row in rows
        ]

    async def index_stats(self, model_id: int) -> tuple[int, str | None]:
        """``(已建向量的条目数, 最后写入时间)``; 库文件不存在时返回 ``(0, None)``, 不为统计创建文件."""
        if not self.exists:
            return (0, None)
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(DISTINCT metadata_id), MAX(created_at) FROM media_embeddings WHERE model_id = ?",
            (model_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return (0, None)
        return (int(row[0] or 0), row[1])

    async def signature(self, model_id: int) -> tuple[int, str | None]:
        """``(行数, 最后写入时间)``; 只给离线工具与测试用, 不进检索路径."""
        if not self.exists:
            return (0, None)
        conn = await self._ensure()
        async with conn.execute(
            "SELECT COUNT(*), MAX(created_at) FROM media_embeddings WHERE model_id = ?", (model_id,)
        ) as cur:
            row = await cur.fetchone()
        return (int(row[0] or 0), row[1]) if row is not None else (0, None)

    async def row_count(self, model_id: int) -> int:
        """当前模型的行数; 索引侧按增长比例决定是否重装, 只在这条路径上查."""
        if not self.exists:
            return 0
        conn = await self._ensure()
        async with conn.execute("SELECT COUNT(*) FROM media_embeddings WHERE model_id = ?", (model_id,)) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row is not None else 0

    async def signatures(self, model_id: int, metadata_id: int | None = None) -> Signatures:
        """按条目分组, 对账时逐条目取用; 建索引时据此跳过未变化的向量."""
        if not self.exists:
            return {}
        conn = await self._ensure()
        sql = "SELECT metadata_id, resource_url, kind, content_hash FROM media_embeddings WHERE model_id = ?"
        params: tuple[object, ...] = (model_id,)
        if metadata_id is not None:
            sql += " AND metadata_id = ?"
            params = (model_id, metadata_id)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        grouped: Signatures = {}
        for row in rows:
            grouped.setdefault(int(row[0]), {})[str(row[1])] = (EmbeddingKind(row[2]), row[3])
        return grouped

    async def load(self, model_id: int, dim: int) -> EmbeddingIndex:
        """装一次快照; 代次在打开读事务之前捕获, 之后提交的写入留给下一轮重装."""
        generation = self._generation
        return await asyncio.to_thread(self._load_snapshot, model_id, dim, generation)

    def _load_snapshot(self, model_id: int, dim: int, generation: int) -> EmbeddingIndex:
        if not self.exists:
            return _empty_index(model_id, dim, generation)
        try:
            conn = sqlite3.connect(f"{self._db_path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
        except sqlite3.Error as exc:
            logger.warning("embedding snapshot open failed", path=str(self._db_path), exc_info=True)
            raise EmbeddingSnapshotError(f"向量库快照打开失败: {self._db_path}") from exc
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            total = int(
                conn.execute("SELECT COUNT(*) FROM media_embeddings WHERE model_id = ?", (model_id,)).fetchone()[0]
            )
            matrix = np.empty((total, dim), dtype=np.float32)
            metadata_ids = np.empty(total, dtype=np.int64)
            kinds = np.empty(total, dtype=np.uint8)
            urls: list[str] = []
            filled = 0
            cursor = conn.execute(
                "SELECT metadata_id, kind, resource_url, vector FROM media_embeddings WHERE model_id = ?", (model_id,)
            )
            while filled < total:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                for metadata_id, kind, url, vector in rows:
                    raw = np.frombuffer(vector, dtype=np.float32)
                    if raw.size != dim:
                        logger.warning("embedding dim mismatch", metadata_id=metadata_id, dim=raw.size, expected=dim)
                        continue
                    matrix[filled] = raw
                    metadata_ids[filled] = metadata_id
                    kinds[filled] = _KIND_CODE[EmbeddingKind(kind)]
                    urls.append(str(url))
                    filled += 1
        except sqlite3.Error as exc:
            logger.warning("embedding snapshot read failed", path=str(self._db_path), exc_info=True)
            raise EmbeddingSnapshotError(f"向量库快照读取失败: {self._db_path}") from exc
        finally:
            conn.close()
        if filled < total:
            logger.warning("embedding snapshot short read", filled=filled, expected=total)
        return EmbeddingIndex(
            model_id=model_id,
            dim=dim,
            generation=generation,
            matrix=matrix[:filled],
            metadata_ids=metadata_ids[:filled],
            kinds=kinds[:filled],
            urls=tuple(urls),
        )

    async def upsert(self, model_id: int, rows: Sequence[EmbeddingWrite]) -> int:
        if not rows:
            return 0
        conn = await self._ensure()
        now = _now()
        cur = await conn.executemany(
            "INSERT INTO media_embeddings"
            " (metadata_id, model_id, kind, resource_url, content_hash, vector, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (metadata_id, model_id, resource_url) DO UPDATE SET"
            " kind = excluded.kind, content_hash = excluded.content_hash,"
            " vector = excluded.vector, created_at = excluded.created_at",
            [
                (
                    row.metadata_id,
                    model_id,
                    str(row.kind),
                    row.resource_url,
                    row.content_hash,
                    _as_bytes(row.vector),
                    now,
                )
                for row in rows
            ],
        )
        await conn.commit()
        if cur.rowcount:
            self._notify()
        return len(rows)

    async def delete_keys(self, model_id: int, keys: Sequence[tuple[int, str]]) -> int:
        """删除指定条目的过期行; ``keys`` 为 ``(metadata_id, resource_url)``."""
        if not keys or not self.exists:
            return 0
        conn = await self._ensure()
        cur = await conn.executemany(
            "DELETE FROM media_embeddings WHERE model_id = ? AND metadata_id = ? AND resource_url = ?",
            [(model_id, metadata_id, url) for metadata_id, url in keys],
        )
        await conn.commit()
        if cur.rowcount:
            self._notify()
        return len(keys)

    async def delete_metadata(self, metadata_ids: Sequence[int], model_id: int | None = None) -> int:
        """删条目的全部向量; ``model_id`` 为 None 时连同其它模型一起删 (级联删除用). 分批绑定, 避开变量上限."""
        if not metadata_ids or not self.exists:
            return 0
        conn = await self._ensure()
        removed = 0
        for start in range(0, len(metadata_ids), DELETE_CHUNK):
            chunk = list(metadata_ids[start : start + DELETE_CHUNK])
            placeholders = ",".join("?" for _ in chunk)
            params: list[object] = [*chunk]
            sql = f"DELETE FROM media_embeddings WHERE metadata_id IN ({placeholders})"
            if model_id is not None:
                sql += " AND model_id = ?"
                params.append(model_id)
            cur = await conn.execute(sql, params)
            removed += max(0, cur.rowcount)
        await conn.commit()
        if removed:
            # 整条删除才意味着条目本身消失; 限定模型的删除只是重建某一张向量.
            self._notify(metadata_ids if model_id is None else ())
        return removed


def _empty_index(model_id: int, dim: int, generation: int) -> EmbeddingIndex:
    return EmbeddingIndex(
        model_id=model_id,
        dim=dim,
        generation=generation,
        matrix=np.zeros((0, dim), dtype=np.float32),
        metadata_ids=np.zeros(0, dtype=np.int64),
        kinds=np.zeros(0, dtype=np.uint8),
        urls=(),
    )
