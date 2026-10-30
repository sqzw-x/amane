import asyncio
from typing import TYPE_CHECKING, Protocol

from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from . import repos

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from .embeddings import EmbeddingStore


class MediaChangeHook(Protocol):
    """条目图片字段 (封面 / 剧照) 变化时的通知; 实现方负责自己的失败隔离."""

    async def metadata_changed(self, metadata_id: int) -> None: ...


class Repository(
    repos.AgentRepoMixin,
    repos.FacetsRepoMixin,
    repos.FeedsRepoMixin,
    repos.LibrariesRepoMixin,
    repos.MediaRepoMixin,
    repos.MetadataRepoMixin,
    repos.SchedulesRepoMixin,
    repos.TasksRepoMixin,
):
    def __init__(self, engine: AsyncEngine, *, embeddings: EmbeddingStore | None = None):
        self._engine = engine
        # 向量在 sidecar 库, 本库不持有其 schema; 级联删除经它清理.
        self._embeddings = embeddings
        self._media_hook: MediaChangeHook | None = None
        # SQLite DEFERRED: 两个 session 的 SELECT 都在写锁前看到空表. 入队互斥检查与 INSERT 同锁.
        self._task_insert_lock = asyncio.Lock()

    @property
    def embeddings(self) -> EmbeddingStore | None:
        return self._embeddings

    def set_media_hook(self, hook: MediaChangeHook | None) -> None:
        """热重载替换语义服务时重设; 元数据写库路径据此重算向量."""
        self._media_hook = hook

    async def notify_media_changed(self, metadata_id: int) -> None:
        if self._media_hook is not None:
            await self._media_hook.metadata_changed(metadata_id)

    async def initialize(self) -> None:
        """``create_all`` 仅补缺表; schema 演进须经 Alembic."""
        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    def _session(self) -> AsyncSession:
        return AsyncSession(self._engine, expire_on_commit=False)
