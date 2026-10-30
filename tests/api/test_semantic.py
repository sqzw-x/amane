"""语义检索端点: 状态统计、来源筛选与错误分支."""

import sys
from typing import TYPE_CHECKING

import numpy as np
import pytest
import pytest_asyncio
from httpx2 import ASGITransport, AsyncClient
from PIL import Image

from amane.api.routes import API_PREFIX
from amane.config import HotSettings
from amane.db import Resource
from amane.db.models import SavedQueryEntity
from amane.semantic import ImageEncoder, SemanticService, SemanticUnavailable, TextEncoder
from amane.semantic.models import (
    DEFAULT_SPEC,
    backend_for_platform,
    download_size,
    tower_sizes,
)
from tests.api.conftest import make_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence
    from pathlib import Path

    from fastapi import FastAPI

    from amane.db import Metadata

MODEL = "siglip2-base-v1"
DIM = 768

_IMAGE_VECTORS = {
    10: [1.0, 0.0, 0.0, 0.0],
    20: [0.0, 1.0, 0.0, 0.0],
    30: [0.7071, 0.7071, 0.0, 0.0],
    40: [0.05, 0.4, 0.0, 0.0],
}


def _vec(values: Sequence[float]) -> np.ndarray:
    vector = np.zeros(DIM, dtype=np.float32)
    vector[: len(values)] = values
    return vector


class _FakeEncoder(ImageEncoder, TextEncoder):
    dim = DIM

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        return np.stack([_vec(_IMAGE_VECTORS[image.size[0]]) for image in images])

    def embed_text(self, text: str) -> np.ndarray:
        return _vec([1.0, 0.0, 0.0, 0.0])


class _FakeService(SemanticService):
    """跳过模型下载与加载, 只测 HTTP 契约与排序."""

    async def _load_vision(self, *, download: bool) -> ImageEncoder:
        return _FakeEncoder()

    async def _load_text(self) -> TextEncoder:
        return _FakeEncoder()


def _install_service(app: FastAPI) -> _FakeService:
    runtime = app.state.runtime
    service = _FakeService(
        runtime.repo, runtime.resource_store, runtime.config.cold.data_dir, runtime.config.hot.semantic
    )
    runtime.semantic = service
    runtime.repo.set_media_hook(service)
    return service


async def _seed_image(app: FastAPI, metadata: Metadata, url: str, width: int) -> None:
    runtime = app.state.runtime
    store = runtime.resource_store
    assert metadata.id is not None
    relative = f"aa/{metadata.id:016x}-{width}.png"
    path = store.full_path(Resource(url=url, file_path=relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, width), (255, 0, 0)).save(path)
    async with runtime.repo._session() as session:
        session.add(Resource(url=url, file_path=relative, content_hash=f"hash-{width}"))
        await session.commit()


@pytest_asyncio.fixture
async def semantic_app(tmp_path: Path) -> AsyncGenerator[FastAPI]:
    app = make_app(HotSettings(), tmp_path / "data", tmp_path / "logs", tmp_path / "files")
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    yield app
    await ctx.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def semantic_client(semantic_app: FastAPI) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=semantic_app)
    async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
        yield client


@pytest.mark.asyncio
async def test_status_does_not_create_store_or_load_model(semantic_app: FastAPI, semantic_client: AsyncClient) -> None:
    response = await semantic_client.get("semantic/status")
    vision_size, text_size = tower_sizes(DEFAULT_SPEC, backend_for_platform())
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "backend": "coreml" if sys.platform == "darwin" else "onnx",
        "backend_label": "Core ML" if sys.platform == "darwin" else "ONNX Runtime",
        "current_model": MODEL,
        "current_model_label": "SigLIP2 Base v1",
        "search_ready": False,
        "index_ready": False,
        "download_size": download_size(DEFAULT_SPEC, backend_for_platform()),
        "vision_size": vision_size,
        "text_size": text_size,
        "model_path": str(semantic_app.state.runtime.config.cold.data_dir / "models" / "siglip2-base" / "v1"),
        "model_bytes": 0,
        "embeddings_path": str(semantic_app.state.runtime.config.cold.data_dir / "embeddings.db"),
        "embeddings_bytes": 0,
        "indexed_metadata": 0,
        "indexed_at": None,
        "text_loaded": False,
        "vision_loaded": False,
        "models": {},
    }
    assert not (semantic_app.state.runtime.config.cold.data_dir / "embeddings.db").exists()


@pytest.mark.asyncio
async def test_status_counts_cover_and_stills(semantic_app: FastAPI, semantic_client: AsyncClient) -> None:
    service = _install_service(semantic_app)
    runtime = semantic_app.state.runtime
    metadata = await runtime.repo.upsert_metadata(
        number="SEM-000",
        poster_urls=["http://example.com/p.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/s.jpg"]},
    )
    await _seed_image(semantic_app, metadata, "http://example.com/p.jpg", 10)
    await _seed_image(semantic_app, metadata, "http://example.com/s.jpg", 20)
    assert (await service.build_index()).indexed == 2

    body = (await semantic_client.get("semantic/status")).json()
    assert body["models"] == {MODEL: {"count": 2, "covers": 1, "stills": 1}}


@pytest.mark.asyncio
async def test_search_orders_by_similarity_and_carries_kind(
    semantic_app: FastAPI, semantic_client: AsyncClient
) -> None:
    service = _install_service(semantic_app)
    runtime = semantic_app.state.runtime
    low = await runtime.repo.upsert_metadata(number="SEM-001", poster_urls=["http://example.com/low.jpg"])
    high = await runtime.repo.upsert_metadata(
        number="SEM-002",
        poster_urls=["http://example.com/high.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/high-still.jpg"]},
    )
    await _seed_image(semantic_app, low, "http://example.com/low.jpg", 20)
    await _seed_image(semantic_app, high, "http://example.com/high.jpg", 10)
    await _seed_image(semantic_app, high, "http://example.com/high-still.jpg", 30)
    assert (await service.build_index()).indexed == 3

    body = (await semantic_client.get("metadata", params={"semantic_query": "红色"})).json()
    assert [item["number"] for item in body["items"]] == ["SEM-002", "SEM-001"]
    assert body["total"] == 2
    hits = {hit["id"]: hit for hit in body["semantic_hits"]}
    assert hits[body["items"][0]["id"]]["kind"] == "cover"
    assert hits[body["items"][0]["id"]]["resource_url"] == "http://example.com/high.jpg"

    stills = (await semantic_client.get("metadata", params={"semantic_query": "红色", "semantic_kind": "still"})).json()
    assert [item["number"] for item in stills["items"]] == ["SEM-002"]
    still_hits = {hit["id"]: hit for hit in stills["semantic_hits"]}
    assert still_hits[stills["items"][0]["id"]]["kind"] == "still"
    assert still_hits[stills["items"][0]["id"]]["resource_url"] == "http://example.com/high-still.jpg"

    covers = (await semantic_client.get("metadata", params={"semantic_query": "红色", "semantic_kind": "cover"})).json()
    assert [item["number"] for item in covers["items"]] == ["SEM-002", "SEM-001"]

    page = (await semantic_client.get("metadata", params={"semantic_query": "红色", "limit": 1, "offset": 1})).json()
    assert [item["number"] for item in page["items"]] == ["SEM-001"]
    assert page["total"] == 2

    beyond = (await semantic_client.get("metadata", params={"semantic_query": "红色", "offset": 5})).json()
    assert beyond["items"] == []
    assert beyond["total"] == 2


@pytest.mark.asyncio
async def test_search_intersects_filters_and_saved_query(
    semantic_app: FastAPI, semantic_client: AsyncClient, tmp_path: Path
) -> None:
    """筛选先收窄候选集, 语义只在该集合内排名; 预设 SQL 走同一路径."""
    service = _install_service(semantic_app)
    runtime = semantic_app.state.runtime
    low = await runtime.repo.upsert_metadata(number="SEM-101", poster_urls=["http://example.com/low.jpg"])
    high = await runtime.repo.upsert_metadata(number="SEM-102", poster_urls=["http://example.com/high.jpg"])
    await _seed_image(semantic_app, low, "http://example.com/low.jpg", 20)
    await _seed_image(semantic_app, high, "http://example.com/high.jpg", 10)
    assert (await service.build_index()).indexed == 2

    (tmp_path / "lib").mkdir()
    library = await runtime.repo.create_library(name="lib", path=str(tmp_path / "lib"))
    assert low.id is not None
    await runtime.repo.create_media_file(
        library_id=library.id, path=str(tmp_path / "lib" / "a.mp4"), metadata_id=low.id
    )

    with_files = (await semantic_client.get("metadata", params={"semantic_query": "红色", "has_files": "true"})).json()
    assert [item["number"] for item in with_files["items"]] == ["SEM-101"]

    without_files = (
        await semantic_client.get("metadata", params={"semantic_query": "红色", "has_files": "false"})
    ).json()
    assert [item["number"] for item in without_files["items"]] == ["SEM-102"]

    saved = await runtime.repo.create_saved_query(
        name="only high",
        sql="SELECT id FROM metadata WHERE number = 'SEM-102'",
        entity=SavedQueryEntity.METADATA,
    )
    filtered = (
        await semantic_client.get("metadata", params={"semantic_query": "红色", "saved_query_id": saved.id})
    ).json()
    assert [item["number"] for item in filtered["items"]] == ["SEM-102"]

    empty = (
        await semantic_client.get(
            "metadata", params={"semantic_query": "红色", "has_files": "true", "saved_query_id": saved.id}
        )
    ).json()
    assert empty["items"] == []


@pytest.mark.asyncio
async def test_search_rejects_unknown_enum_params(semantic_app: FastAPI, semantic_client: AsyncClient) -> None:
    _install_service(semantic_app)
    assert (
        await semantic_client.get("metadata", params={"semantic_query": "红色", "semantic_kind": "bogus"})
    ).status_code == 422
    assert (
        await semantic_client.get("metadata", params={"semantic_query": "红色", "definition": "bogus"})
    ).status_code == 422


@pytest.mark.asyncio
async def test_search_reports_model_not_downloaded(semantic_client: AsyncClient) -> None:
    """真实服务 + 空数据目录: 检索直接 503, 不下载模型."""
    response = await semantic_client.get("metadata", params={"semantic_query": "红色"})
    assert response.status_code == 503
    assert "尚未下载" in response.json()["detail"]


@pytest.mark.asyncio
async def test_search_reports_unavailable_model(semantic_app: FastAPI) -> None:
    runtime = semantic_app.state.runtime

    class _Broken(_FakeService):
        async def _load_text(self) -> TextEncoder:
            raise SemanticUnavailable("tokenizer 文件不存在: tokenizer.json")

    broken = _Broken(runtime.repo, runtime.resource_store, runtime.config.cold.data_dir, runtime.config.hot.semantic)
    runtime.semantic = broken
    transport = ASGITransport(app=semantic_app)
    async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as client:
        response = await client.get("metadata", params={"semantic_query": "红色"})
    assert response.status_code == 503
    assert "不存在" in response.json()["detail"]


@pytest.mark.asyncio
async def test_release_drops_memory_index_but_keeps_vectors(
    semantic_app: FastAPI, semantic_client: AsyncClient
) -> None:
    service = _install_service(semantic_app)
    runtime = semantic_app.state.runtime
    metadata = await runtime.repo.upsert_metadata(number="SEM-RL", poster_urls=["http://example.com/r.jpg"])
    await _seed_image(semantic_app, metadata, "http://example.com/r.jpg", 10)
    assert (await service.build_index()).indexed == 1
    page = (await semantic_client.get("metadata", params={"semantic_query": "红色"})).json()
    assert [item["number"] for item in page["items"]] == ["SEM-RL"]
    assert service._snapshot is not None

    body = (await semantic_client.post("semantic/release")).json()

    assert body["models"] == {MODEL: {"count": 1, "covers": 1, "stills": 0}}
    assert body["text_loaded"] is False
    assert service._snapshot is None
    assert service._text is None

    # 向量还在库里: 下一次检索自己装回来.
    page = (await semantic_client.get("metadata", params={"semantic_query": "红色"})).json()
    assert [item["number"] for item in page["items"]] == ["SEM-RL"]
