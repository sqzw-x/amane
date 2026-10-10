"""CLEANUP handler - 悬空 MediaFile 索引 / 未引用 Resource 回收; 不删 Metadata."""

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from amane.db.models import FacetKind, MediaFileStatus
from amane.db.repository import Repository
from amane.handlers import CleanupHandler, CleanupPayload
from amane.media import ResourceStore
from amane.media.resource_store import RESOURCE_URL_PREFIX
from amane.parsing import EMPTY_NUMBER_RULES

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest_asyncio.fixture
async def cleanup_env(tmp_path: Path) -> AsyncGenerator[tuple[Repository, ResourceStore, Path]]:
    """Repo 与 ResourceStore 共用同一 engine (与生产一致)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    repo = Repository(engine)
    store = ResourceStore(engine=engine, base_dir=tmp_path / "resources")
    yield repo, store, tmp_path
    await engine.dispose()


async def _seed_resource(store: ResourceStore, url: str) -> None:
    async def producer(dest: Path) -> bool:
        Image.new("RGB", (100, 100), "blue").save(dest)
        return True

    # acquire_derived 可在无 HTTP 下写入任意 locator; 用 crop 造外部图等价记录较重,
    # 这里直接用 derived locator 模拟已下载资源文件.
    res = await store.acquire_derived(url, "crop", "seed", producer)
    assert res is not None


@pytest.mark.asyncio
async def test_removes_missing_media_files_keeps_metadata(cleanup_env):
    repo, store, tmp_path = cleanup_env
    lib = await repo.create_library(name="L", path=str(tmp_path / "lib"))
    assert lib.id is not None

    meta = await repo.upsert_metadata(number="ABC-001", title="t")
    assert meta.id is not None

    ghost = tmp_path / "gone.mp4"
    mf = await repo.create_media_file(
        lib.id,
        path=str(ghost),
        number="ABC-001",
        status=MediaFileStatus.SCRAPED,
        metadata_id=meta.id,
        rules=EMPTY_NUMBER_RULES,
    )
    assert mf.id is not None
    assert not ghost.exists()

    handler = CleanupHandler(repo, store)
    result = await handler.handle(CleanupPayload(remove_missing_files=True, remove_unreferenced_resources=False))
    assert result.success
    assert result.result is not None
    assert result.result.files_removed == 1
    assert await repo.get_media_file(mf.id) is None
    assert await repo.get_metadata(meta.id) is not None


@pytest.mark.asyncio
async def test_missing_file_exists_does_not_block_event_loop(cleanup_env, monkeypatch):
    """exists 在线程池: 库路径在 FUSE 上时 CLEANUP 不能阻塞事件循环."""
    repo, store, tmp_path = cleanup_env
    lib = await repo.create_library(name="L", path=str(tmp_path / "lib"))
    assert lib.id is not None
    ghost = tmp_path / "gone.mp4"
    await repo.create_media_file(lib.id, path=str(ghost), rules=EMPTY_NUMBER_RULES)

    order: list[str] = []
    real_exists = Path.exists

    def slow_exists(self, *args, **kwargs):
        order.append("exists_start")
        time.sleep(0.2)
        order.append("exists_end")
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", slow_exists)

    async def marker() -> None:
        await asyncio.sleep(0.05)
        order.append("marker")

    handler = CleanupHandler(repo, store)
    result, _ = await asyncio.gather(
        handler.handle(CleanupPayload(remove_missing_files=True, remove_unreferenced_resources=False)), marker()
    )
    assert result.success
    assert "marker" in order
    assert order.index("marker") < order.index("exists_end")


@pytest.mark.asyncio
async def test_does_not_delete_metadata_without_media_files(cleanup_env):
    repo, store, _tmp = cleanup_env
    meta = await repo.upsert_metadata(number="XYZ-9", title="solo")
    assert meta.id is not None

    handler = CleanupHandler(repo, store)
    result = await handler.handle(CleanupPayload())
    assert result.success
    assert await repo.get_metadata(meta.id) is not None


@pytest.mark.asyncio
async def test_purges_unreferenced_resources(cleanup_env):
    repo, store, _tmp = cleanup_env

    async def producer(dest: Path) -> bool:
        Image.new("RGB", (100, 100), "blue").save(dest)
        return True

    keep = await store.acquire_derived("https://cdn.example/a.jpg", "crop", "keep", producer)
    drop = await store.acquire_derived("https://cdn.example/b.jpg", "crop", "drop", producer)
    assert keep is not None and drop is not None
    drop_path = store.full_path(drop)

    await repo.upsert_metadata(
        number="KEEP-1",
        thumb_urls=[keep.url],
        poster_urls=[f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(keep.url)}"],
    )

    handler = CleanupHandler(repo, store)
    result = await handler.handle(CleanupPayload(remove_missing_files=False, remove_unreferenced_resources=True))
    assert result.success
    assert result.result is not None
    assert result.result.resources_removed == 1
    assert await store.get_by_url(keep.url) is not None
    assert await store.get_by_url(drop.url) is None
    assert not drop_path.exists()


@pytest.mark.asyncio
async def test_keeps_actor_internal_resource(cleanup_env):
    """演员 image_urls 中的内部 URL 保活派生资源; 未引用的同型资源照常回收."""
    repo, store, _tmp = cleanup_env

    async def producer(dest: Path) -> bool:
        Image.new("RGB", (100, 100), "blue").save(dest)
        return True

    keep = await store.acquire_derived("https://cdn.example/actor.jpg", "crop", "box:0,0,50,50", producer)
    drop = await store.acquire_derived("https://cdn.example/other.jpg", "crop", "box:0,0,50,50", producer)
    assert keep is not None and drop is not None

    await repo.upsert_metadata(number="ACT-KEEP-1", actors=["KeepActor"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    actor_id = next(a.id for a in actors if a.name == "KeepActor")
    actor = await repo.get_actor(actor_id)
    assert actor is not None
    actor.image_urls = [f"{RESOURCE_URL_PREFIX}/{ResourceStore.url_hash(keep.url)}", "https://cdn.example/actor.jpg"]
    await repo.save_actor(actor)

    handler = CleanupHandler(repo, store)
    result = await handler.handle(CleanupPayload(remove_missing_files=False, remove_unreferenced_resources=True))

    assert result.success
    assert result.result is not None
    assert result.result.resources_removed == 1
    assert await store.get_by_url(keep.url) is not None
    assert await store.get_by_url(drop.url) is None


@pytest.mark.asyncio
async def test_resource_gc_disabled(cleanup_env):
    repo, store, _tmp = cleanup_env
    await _seed_resource(store, "https://cdn.example/orphan.jpg")
    assert len(await store.list_all()) == 1

    handler = CleanupHandler(repo, store)
    result = await handler.handle(CleanupPayload(remove_missing_files=False, remove_unreferenced_resources=False))
    assert result.success
    assert result.result is not None
    assert result.result.resources_removed == 0
    assert len(await store.list_all()) == 1
