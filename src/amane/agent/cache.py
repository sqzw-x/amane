"""按 saved_query_id 的 LRU + TTL 内存缓存."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CachedResult:
    """缓存条目绑定产生它的 SQL; 行列内容本身无感."""

    saved_query_id: int
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    created_at: float = field(default_factory=time.monotonic)

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ResultCache:
    """调整容量不丢弃现有未过期条目."""

    def __init__(self, *, ttl_s: int = 3600, max_entries: int = 64) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: OrderedDict[int, CachedResult] = OrderedDict()

    def configure(self, *, ttl_s: int, max_entries: int) -> None:
        """不丢弃现有未过期条目, 立刻按新上限裁剪."""
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._evict()

    def get(self, saved_query_id: int, sql: str) -> CachedResult | None:
        """仅命中 SQL 与当前预设一致的条目.

        预设 id 是 rowid, 删除后会被新行复用; 执行中的旧请求也可能在失效之后回写.
        以条目 SQL 为版本, 这两种情况下的旧条目都不会命中, 无须依赖失效调用的时序.
        """
        entry = self._entries.get(saved_query_id)
        if entry is None or entry.sql != sql:
            return None
        if time.monotonic() - entry.created_at > self._ttl_s:
            del self._entries[saved_query_id]
            return None
        self._entries.move_to_end(saved_query_id)
        return entry

    def put(self, entry: CachedResult) -> None:
        self._entries[entry.saved_query_id] = entry
        self._entries.move_to_end(entry.saved_query_id)
        self._evict()

    def invalidate(self, saved_query_id: int) -> None:
        self._entries.pop(saved_query_id, None)

    def clear(self) -> None:
        self._entries.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._entries.items() if now - v.created_at > self._ttl_s]
        for k in expired:
            del self._entries[k]
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
