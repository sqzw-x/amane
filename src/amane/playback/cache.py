"""Process-local TTL + singleflight for playback probe and open failures."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

T = TypeVar("T")

OPEN_FAIL_TTL_SECONDS = 3.0
PROBE_FAIL_TTL_SECONDS = 15.0
PROBE_NONE_TTL_SECONDS = 60.0
PROBE_HIT_TTL_SECONDS = 30.0
MAX_ENTRIES = 4096


class _TtlMap:
    def __init__(self, *, ttl_seconds: float, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def get(self, key: str) -> object | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if time.monotonic() >= expires:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: str, value: object) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._items.items() if now >= exp]
        for k in expired:
            del self._items[k]
        self._items[key] = (now + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def is_blocked(self, key: str) -> bool:
        return self.get(key) is not None


class PlaybackCaches:
    """Independent TTLs for probe hits, probe none, probe errors, and open failures."""

    def __init__(self) -> None:
        self.probe_hits = _TtlMap(ttl_seconds=PROBE_HIT_TTL_SECONDS)
        self.probe_none = _TtlMap(ttl_seconds=PROBE_NONE_TTL_SECONDS)
        self.probe_fail = _TtlMap(ttl_seconds=PROBE_FAIL_TTL_SECONDS)
        self.open_fail = _TtlMap(ttl_seconds=OPEN_FAIL_TTL_SECONDS)
        self._inflight: dict[str, asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

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
