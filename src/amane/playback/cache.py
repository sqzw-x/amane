"""Process-local TTL + singleflight for probe, open failures, and resolved targets."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from ..plugins.api import PlaybackTarget

T = TypeVar("T")

OPEN_FAIL_TTL_SECONDS = 3.0
PROBE_HIT_TTL_SECONDS = 30.0
RESOLVE_TTL_MAX_SECONDS = 300.0
MAX_ENTRIES = 4096


class _TtlMap[ValueT]:
    """按 key 的 TTL 表.

    ``ttl_seconds`` 是这张表的缺省档位; ``put(..., ttl=...)`` 可给单条记录自己的有效期 ——
    解析结果的有效期逐条由插件声明, 钉不到固定档位上.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, ValueT]] = OrderedDict()

    def get(self, key: str) -> ValueT | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if time.monotonic() >= expires:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: str, value: ValueT, *, ttl: float | None = None) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._items.items() if now >= exp]
        for k in expired:
            del self._items[k]
        self._items[key] = (now + (self._ttl if ttl is None else ttl), value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def is_blocked(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        self._items.clear()


class PlaybackCaches:
    """Independent TTLs for probe hits, open failures, and resolved targets.

    没有探测失败的负缓存: 探测由用户切到某个来源时触发, 每次点击最多一次插件调用, 而负缓存会让
    「刚修好又点一次」拿到过期的不可用结论.
    """

    def __init__(self) -> None:
        self.probe_hits: _TtlMap[object] = _TtlMap(ttl_seconds=PROBE_HIT_TTL_SECONDS)
        self.open_fail: _TtlMap[object] = _TtlMap(ttl_seconds=OPEN_FAIL_TTL_SECONDS)
        # 解析结果的有效期逐条由插件声明 (上限 RESOLVE_TTL_MAX_SECONDS), 缺省档位只用于兜底.
        self.resolve_hits: _TtlMap[PlaybackTarget] = _TtlMap(ttl_seconds=RESOLVE_TTL_MAX_SECONDS)
        self._inflight: dict[str, asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """丢弃全部 TTL 记录; 插件集合变化时调用. 在途合并由 ``_inflight`` 自行结束."""
        self.probe_hits.clear()
        self.open_fail.clear()
        self.resolve_hits.clear()

    async def coalesce(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                wait_fut = existing
                is_leader = False
            else:
                wait_fut = asyncio.get_running_loop().create_future()
                self._inflight[key] = wait_fut
                is_leader = True

        if not is_leader:
            return cast(T, await wait_fut)

        try:
            result = await factory()
            wait_fut.set_result(result)
            return result
        except BaseException as exc:
            if not wait_fut.done():
                wait_fut.set_exception(exc)
            raise
        finally:
            async with self._lock:
                if self._inflight.get(key) is wait_fut:
                    del self._inflight[key]
