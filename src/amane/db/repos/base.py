from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterator

    from ..embeddings import EmbeddingStore
    from ..repository import MediaChangeHook

# SQLite 单条语句的绑定变量上限 (3.32+ 为 32766). 500 给语句里其它占位留余量.
SQL_IN_CHUNK_SIZE = 500


def chunks[T](items: list[T]) -> Iterator[list[T]]:
    """按 ``SQL_IN_CHUNK_SIZE`` 分批绑定数量随数据增长的 id 列表.

    整批 id 放进一条语句会超过 SQLite 的绑定变量上限, 报 ``too many SQL variables``.
    """
    for start in range(0, len(items), SQL_IN_CHUNK_SIZE):
        yield items[start : start + SQL_IN_CHUNK_SIZE]


class RepositoryMixinBase:
    """子类须提供 ``_session()``. 任务 mixin 还依赖 ``_task_insert_lock`` 串行化入队互斥."""

    _task_insert_lock: asyncio.Lock
    # 向量 sidecar 与图片变化的通知; 仅条目写路径与级联删除使用.
    _embeddings: EmbeddingStore | None
    _media_hook: MediaChangeHook | None

    def _session(self) -> AsyncSession:
        raise NotImplementedError

    async def notify_media_changed(self, metadata_id: int) -> None:
        raise NotImplementedError
