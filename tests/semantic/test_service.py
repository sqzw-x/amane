"""SemanticService: 幂等索引、剧照被动索引、失效与检索排序 (编码器用假实现, 不加载模型)."""

import asyncio
import hashlib
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
import pytest
import pytest_asyncio
from PIL import Image

from amane.config import SemanticConfig
from amane.db import Metadata, Resource
from amane.db.embeddings import (
    EmbeddingIndex,
    EmbeddingKind,
    EmbeddingSnapshotError,
    EmbeddingStore,
    EmbeddingWrite,
    Signatures,
)
from amane.media import ResourceStore
from amane.semantic import ImageEncoder, SemanticService, SemanticUnavailable, TextEncoder
from amane.semantic import service as service_module

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence
    from pathlib import Path

    from amane.db import Metadata, Repository


MODEL = "siglip2-base-v1"
"""与 ``models.DEFAULT_SPEC`` 一致, 服务据此注册模型行."""

DIM = 768

# 图片宽度 → 前四维向量; 其余维度保持 0. 数值决定排序, 与真实模型一样归一化后比较.
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
    """图像向量由图片宽度决定; 文本向量按词查表, 未登记的词指向第一维. ``encoded`` 记累计编码张数."""

    dim = DIM

    def __init__(self, texts: dict[str, list[float]] | None = None) -> None:
        self._texts = texts or {}
        self.encoded = 0

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.encoded += len(images)
        return np.stack([_vec(_IMAGE_VECTORS[image.size[0]]) for image in images])

    def embed_text(self, text: str) -> np.ndarray:
        return _vec(self._texts.get(text, [1.0, 0.0, 0.0, 0.0]))


class _FakeService(SemanticService):
    """两个塔都换成同一个假编码器, 跳过下载与加载; ``ready`` 模拟本地模型是否就绪."""

    fake: _FakeEncoder
    ready: bool = True

    async def _load_vision(self, *, download: bool) -> ImageEncoder:
        return self.fake

    async def _load_text(self) -> TextEncoder:
        return self.fake

    @property
    def search_ready(self) -> bool:
        return self.ready

    @property
    def index_ready(self) -> bool:
        return self.ready


def _keys(signatures: Signatures) -> set[tuple[int, str]]:
    return {(metadata_id, url) for metadata_id, rows in signatures.items() for url in rows}


def _embeddings(repo: Repository) -> EmbeddingStore:
    store = repo.embeddings
    assert store is not None
    return store


_ACTIVE: list[SemanticService] = []


def _service(repo: Repository, store: ResourceStore, *, auto_maintain: bool = False) -> _FakeService:
    service = _FakeService(repo, store, store.data_dir, SemanticConfig(auto_maintain=auto_maintain))
    service.fake = _FakeEncoder()
    repo.set_media_hook(service)
    _ACTIVE.append(service)
    return service


@pytest_asyncio.fixture(autouse=True)
async def _close_services() -> AsyncGenerator[None]:
    """服务在构造时订阅写通知; 测试结束统一断开, 免得后台重装任务跨用例残留."""
    yield
    while _ACTIVE:
        _ACTIVE.pop().close()


async def _seed_image(
    repo: Repository, store: ResourceStore, metadata: Metadata, url: str, width: int, *, payload: bytes | None = None
) -> None:
    assert metadata.id is not None
    relative = f"aa/{metadata.id:016x}-{hashlib.sha256(url.encode()).hexdigest()[:16]}.png"
    path = store.full_path(Resource(url=url, file_path=relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        Image.new("RGB", (width, width), (255, 0, 0)).save(path)
    else:
        path.write_bytes(payload)
    async with repo._session() as session:
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        session.add(Resource(url=url, file_path=relative, content_hash=f"hash-{digest}"))
        await session.commit()


@pytest.mark.asyncio
async def test_build_index_skips_unchanged_and_reports_missing(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="AAA-001", poster_urls=["http://example.com/a.jpg"])
    await repo.upsert_metadata(number="AAA-002")
    third = await repo.upsert_metadata(number="AAA-003", thumb_urls=["http://example.com/c.jpg"])
    await _seed_image(repo, store, first, "http://example.com/a.jpg", 10)
    await _seed_image(repo, store, third, "http://example.com/c.jpg", 20)

    service = _service(repo, store)
    stats = await service.build_index()
    assert (stats.scanned, stats.indexed, stats.skipped, stats.missing) == (3, 2, 0, 1)

    again = await service.build_index()
    assert (again.indexed, again.skipped, again.missing) == (0, 2, 1)

    hits = await service.search("红色", limit=2)
    assert [hit.metadata_id for hit in hits] == [first.id, third.id]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_content_hash_change_reindexes(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="BBB-001", poster_urls=["http://example.com/b.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/b.jpg", 10)

    service = _service(repo, store)
    assert (await service.build_index()).indexed == 1

    resource = await store.get_by_url("http://example.com/b.jpg")
    assert resource is not None
    resource.content_hash = "changed"
    async with repo._session() as session:
        session.add(resource)
        await session.commit()

    assert (await service.build_index()).indexed == 1


@pytest.mark.asyncio
async def test_stills_are_indexed_passively_and_filtered_by_kind(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    cover_only = await repo.upsert_metadata(number="SSS-001", poster_urls=["http://example.com/c1.jpg"])
    with_stills = await repo.upsert_metadata(
        number="SSS-002",
        poster_urls=["http://example.com/c2.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/s1.jpg", "http://example.com/s2.jpg", "file:///missing.jpg"]},
    )
    await _seed_image(repo, store, cover_only, "http://example.com/c1.jpg", 10)
    await _seed_image(repo, store, with_stills, "http://example.com/c2.jpg", 20)
    await _seed_image(repo, store, with_stills, "http://example.com/s1.jpg", 30)
    await _seed_image(repo, store, with_stills, "http://example.com/s2.jpg", 40)

    service = _service(repo, store)
    stats = await service.build_index()
    assert (stats.scanned, stats.indexed, stats.missing) == (2, 4, 0)

    covers = await service.search("红色", limit=10, kind=EmbeddingKind.COVER)
    assert {hit.metadata_id for hit in covers} == {cover_only.id, with_stills.id}
    assert {hit.kind for hit in covers} == {EmbeddingKind.COVER}

    stills = await service.search("红色", limit=10, kind=EmbeddingKind.STILL)
    assert [hit.metadata_id for hit in stills] == [with_stills.id]
    assert stills[0].kind is EmbeddingKind.STILL
    assert stills[0].resource_url.startswith("http://example.com/s")

    # 同一条目只保留名次最高的那张图, 即便筛选全部来源也不会重复出现.
    everything = await service.search("红色", limit=10)
    assert [hit.metadata_id for hit in everything] == [cover_only.id, with_stills.id]


@pytest.mark.asyncio
async def test_undecodable_image_skips_only_itself(repo: Repository, tmp_path: Path) -> None:
    """一批超过 BATCH 的剧照里混入坏图: 只计失败, 其余照常写入且下标不错位."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    stills = [f"http://example.com/s{index}.jpg" for index in range(15)]
    metadata = await repo.upsert_metadata(
        number="BBB-002",
        poster_urls=["http://example.com/cover.jpg"],
        extrafanart_urls={"dmm": stills},
    )
    await _seed_image(repo, store, metadata, "http://example.com/cover.jpg", 10)
    for position, url in enumerate(stills):
        payload = b"not an image" if position == 0 else None
        await _seed_image(repo, store, metadata, url, 20, payload=payload)

    service = _service(repo, store)
    stats = await service.build_index()
    assert (stats.scanned, stats.indexed, stats.failed) == (1, 15, 1)

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert len(_keys(await _embeddings(repo).signatures(model.id))) == 15


@pytest.mark.asyncio
async def test_stale_still_rows_are_removed_on_reconcile(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="TTT-001",
        poster_urls=["http://example.com/c.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/s1.jpg"]},
    )
    await _seed_image(repo, store, metadata, "http://example.com/c.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/s1.jpg", 20)

    service = _service(repo, store)
    assert (await service.build_index()).indexed == 2
    assert metadata.id is not None
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert len(_keys(await _embeddings(repo).signatures(model.id, metadata.id))) == 2

    # 直接改库绕过失效钩子, 只验证对账本身; 钩子行为见下面两个用例.
    async with repo._session() as session:
        stored = await session.get(Metadata, metadata.id)
        assert stored is not None
        stored.extrafanart_urls = {"dmm": []}
        session.add(stored)
        await session.commit()

    stats = await service.build_index()
    assert (stats.indexed, stats.skipped) == (0, 1)
    assert _keys(await _embeddings(repo).signatures(model.id, metadata.id)) == {
        (metadata.id, "http://example.com/c.jpg")
    }


@pytest.mark.asyncio
async def test_force_reindexes_everything_but_still_reconciles(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="TTT-002",
        poster_urls=["http://example.com/c.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/s1.jpg"]},
    )
    await _seed_image(repo, store, metadata, "http://example.com/c.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/s1.jpg", 20)
    service = _service(repo, store)
    assert (await service.build_index()).indexed == 2
    assert metadata.id is not None

    async with repo._session() as session:
        stored = await session.get(Metadata, metadata.id)
        assert stored is not None
        stored.extrafanart_urls = {"dmm": []}
        session.add(stored)
        await session.commit()

    stats = await service.build_index(force=True)
    assert (stats.indexed, stats.skipped) == (1, 0)
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert _keys(await _embeddings(repo).signatures(model.id, metadata.id)) == {
        (metadata.id, "http://example.com/c.jpg")
    }


@pytest.mark.asyncio
async def test_auto_maintain_off_only_invalidates(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="UUU-001", poster_urls=["http://example.com/old.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/old.jpg", 10)
    service = _service(repo, store, auto_maintain=False)
    assert (await service.build_index()).indexed == 1
    assert metadata.id is not None
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)

    assert await repo.update_metadata(metadata.id, poster_urls=["http://example.com/new.jpg"]) is not None

    # auto_maintain 关闭: 只删不建.
    assert await _embeddings(repo).signatures(model.id, metadata.id) == {}


@pytest.mark.asyncio
async def test_unrelated_metadata_change_keeps_vectors(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="VVV-002", poster_urls=["http://example.com/keep.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/keep.jpg", 10)
    service = _service(repo, store, auto_maintain=False)
    assert (await service.build_index()).indexed == 1
    assert metadata.id is not None
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)

    assert await repo.update_metadata(metadata.id, title="新标题") is not None

    assert _keys(await _embeddings(repo).signatures(model.id, metadata.id)) == {
        (metadata.id, "http://example.com/keep.jpg")
    }


@pytest.mark.asyncio
async def test_auto_maintain_on_reindexes_changed_entry(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="VVV-001", poster_urls=["http://example.com/old.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/old.jpg", 10)
    service = _service(repo, store, auto_maintain=True)
    assert (await service.build_index()).indexed == 1
    assert metadata.id is not None
    await _seed_image(repo, store, metadata, "http://example.com/new.jpg", 20)

    assert await repo.update_metadata(metadata.id, poster_urls=["http://example.com/new.jpg"]) is not None

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    signatures = await _embeddings(repo).signatures(model.id, metadata.id)
    assert _keys(signatures) == {(metadata.id, "http://example.com/new.jpg")}


@pytest.mark.asyncio
async def test_manual_index_releases_encoder(repo: Repository, tmp_path: Path) -> None:
    """auto_maintain 关闭时全量索引算完即释放模型; 打开时保留以承接增量."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="XXX-001", poster_urls=["http://example.com/x.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/x.jpg", 10)

    service = SemanticService(repo, store, store.data_dir, SemanticConfig())
    service._vision = _FakeEncoder()
    await service.build_index()
    assert service._vision is None

    kept = SemanticService(repo, store, store.data_dir, SemanticConfig(auto_maintain=True))
    kept._vision = _FakeEncoder()
    await kept.build_index()
    assert kept._vision is not None


@pytest.mark.asyncio
async def test_cascade_delete_removes_sidecar_rows(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="WWW-001", poster_urls=["http://example.com/w.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/w.jpg", 10)
    service = _service(repo, store)
    assert (await service.build_index()).indexed == 1
    assert metadata.id is not None
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert await _embeddings(repo).signatures(model.id) != {}

    assert await repo.delete_metadata(metadata.id)

    assert await _embeddings(repo).signatures(model.id) == {}


@pytest.mark.asyncio
async def test_status_reports_counts_per_model(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    service = _service(repo, store)
    status = await service.status()
    assert (status.enabled, status.model, status.models) == (True, MODEL, [])
    # 真实服务只查本地文件; 测试数据目录里没有模型.
    assert SemanticService(repo, store, store.data_dir, SemanticConfig()).search_ready is False

    metadata = await repo.upsert_metadata(
        number="YYY-001",
        poster_urls=["http://example.com/y.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/y1.jpg"]},
    )
    await _seed_image(repo, store, metadata, "http://example.com/y.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/y1.jpg", 20)
    assert (await service.build_index()).indexed == 2

    status = await service.status()
    assert [(stats.ref.name, stats.covers, stats.stills) for stats in status.models] == [("siglip2-base", 1, 1)]


@pytest.mark.asyncio
async def test_search_ranks_only_inside_allowed_ids(repo: Repository, tmp_path: Path) -> None:
    """整句只编码一次; 外部筛选出的候选集决定排名范围, 传到 service 的就是最终集合."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="MMM-001", poster_urls=["http://example.com/1.jpg"])
    both = await repo.upsert_metadata(number="MMM-002", poster_urls=["http://example.com/2.jpg"])
    second = await repo.upsert_metadata(number="MMM-003", poster_urls=["http://example.com/3.jpg"])
    assert first.id is not None and both.id is not None and second.id is not None
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    await _seed_image(repo, store, both, "http://example.com/2.jpg", 30)
    await _seed_image(repo, store, second, "http://example.com/3.jpg", 20)

    service = _service(repo, store)
    service.fake = _FakeEncoder({"红色 内衣": [1.0, 0.5, 0.0, 0.0]})
    assert (await service.build_index()).indexed == 3

    hits = await service.search("红色 内衣", limit=3)
    assert [hit.metadata_id for hit in hits] == [both.id, first.id, second.id]

    filtered = await service.search("红色 内衣", limit=3, allowed_ids=[first.id])
    assert [hit.metadata_id for hit in filtered] == [first.id]
    assert await service.search("红色 内衣", limit=3, allowed_ids=[]) == []

    page = await service.search("红色 内衣", limit=1, offset=2)
    assert [hit.metadata_id for hit in page] == [second.id]
    assert await service.count() == 3
    assert await service.count(allowed_ids=[first.id, second.id]) == 2


@pytest.mark.asyncio
async def test_search_without_downloaded_text_tower_fails_without_downloading(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    service = SemanticService(repo, store, store.data_dir, SemanticConfig())
    with pytest.raises(SemanticUnavailable, match="尚未下载"):
        await service.search("红色", limit=5)
    assert not (store.data_dir / "models").exists()
    assert repo.embeddings is not None and not repo.embeddings.exists


@pytest.mark.parametrize(
    ("sites", "expected"),
    [
        # 高优先级站点没有落盘图: 退到下一个站点.
        ({"dmm": ["http://example.com/missing.jpg"], "javbus": ["http://example.com/b1.jpg"]}, {"b1"}),
        # 两个站点都有落盘图: 只取第一个, 与 acquire_extrafanart 的「有结果即停」一致.
        ({"dmm": ["http://example.com/a1.jpg"], "javbus": ["http://example.com/b1.jpg"]}, {"a1"}),
        ({"dmm": ["http://example.com/missing.jpg"]}, set()),
    ],
)
@pytest.mark.asyncio
async def test_stills_come_from_first_site_with_resources(
    repo: Repository, tmp_path: Path, sites: dict[str, list[str]], expected: set[str]
) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="PPP-001", poster_urls=["http://example.com/c.jpg"], extrafanart_urls=sites
    )
    await _seed_image(repo, store, metadata, "http://example.com/c.jpg", 10)
    for name in ("a1", "b1"):
        await _seed_image(repo, store, metadata, f"http://example.com/{name}.jpg", 20)
    assert metadata.id is not None

    await _service(repo, store).build_index()

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    rows = (await _embeddings(repo).signatures(model.id, metadata.id))[metadata.id]
    stills = {
        url.rsplit("/", 1)[-1].removesuffix(".jpg") for url, (kind, _) in rows.items() if kind is EmbeddingKind.STILL
    }
    assert stills == expected


@pytest.mark.parametrize("auto_maintain", [False, True])
@pytest.mark.asyncio
async def test_invalidation_only_touches_changed_images(repo: Repository, tmp_path: Path, auto_maintain: bool) -> None:
    """只换剧照: 封面行原样保留; auto_maintain 打开时只编码新剧照."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="QQQ-001",
        poster_urls=["http://example.com/c.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/s1.jpg"]},
    )
    for url, width in (("c", 10), ("s1", 20), ("s2", 30)):
        await _seed_image(repo, store, metadata, f"http://example.com/{url}.jpg", width)
    service = _service(repo, store, auto_maintain=auto_maintain)
    assert (await service.build_index()).indexed == 2
    assert metadata.id is not None
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    before = (await _embeddings(repo).signatures(model.id, metadata.id))[metadata.id]
    encoded = service.fake.encoded

    await repo.update_metadata(metadata.id, extrafanart_urls={"dmm": ["http://example.com/s2.jpg"]})

    rows = (await _embeddings(repo).signatures(model.id, metadata.id))[metadata.id]
    assert rows["http://example.com/c.jpg"] == before["http://example.com/c.jpg"]
    assert "http://example.com/s1.jpg" not in rows
    assert ("http://example.com/s2.jpg" in rows) is auto_maintain
    assert service.fake.encoded - encoded == (1 if auto_maintain else 0)


@pytest.mark.asyncio
async def test_auto_maintain_skips_encoding_when_model_missing(repo: Repository, tmp_path: Path) -> None:
    """模型未下载: 增量维护只删旧行, 不触发下载或加载."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="QQQ-002", poster_urls=["http://example.com/old.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/old.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/new.jpg", 20)
    service = _service(repo, store, auto_maintain=True)
    assert (await service.build_index()).indexed == 1
    assert metadata.id is not None
    service.ready = False
    encoded = service.fake.encoded

    await repo.update_metadata(metadata.id, poster_urls=["http://example.com/new.jpg"])

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert await _embeddings(repo).signatures(model.id, metadata.id) == {}
    assert service.fake.encoded == encoded


@pytest.mark.asyncio
async def test_full_index_removes_orphan_rows(repo: Repository, tmp_path: Path) -> None:
    """主库已删、sidecar 清理失败留下的行: 完整遍历时清掉, 带 limit 的遍历不动."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    service = _service(repo, store)
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    await _embeddings(repo).upsert(
        model.id, [EmbeddingWrite(999, EmbeddingKind.COVER, "http://x/o.jpg", None, _vec([1.0]))]
    )

    await service.build_index(limit=1)
    assert _keys(await _embeddings(repo).signatures(model.id)) == {(999, "http://x/o.jpg")}

    await service.build_index()
    assert await _embeddings(repo).signatures(model.id) == {}


@pytest.mark.asyncio
async def test_text_tower_is_released_after_idle(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索侧只用文本塔; 空闲超时只释放文本塔, 内存索引常驻."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="IDL-001", poster_urls=["http://example.com/i.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/i.jpg", 10)
    monkeypatch.setattr(service_module, "TEXT_IDLE_SECONDS", 0)
    service = _service(repo, store)
    await service.build_index()
    assert await service.search("红色", limit=5)

    await asyncio.sleep(0.05)

    assert service._text is None
    assert service._snapshot is not None


@pytest.mark.asyncio
async def test_concurrent_image_encoding_is_serialized(repo: Repository, tmp_path: Path) -> None:
    """视觉塔实例不保证线程安全: 索引任务与增量维护并发时不能同时推理."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="ZZZ-001", poster_urls=["http://example.com/z.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/z.jpg", 10)
    resource = await store.get_by_url("http://example.com/z.jpg")
    assert resource is not None
    path = store.full_path(resource)

    active = 0
    peak = 0

    class _Probe(_FakeEncoder):
        def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.05)
            try:
                return super().embed_images(images)
            finally:
                active -= 1

    service = _service(repo, store)
    service.fake = _Probe()
    await asyncio.gather(
        service._embed_images([path], download=True),
        service._embed_images([path], download=True),
    )
    assert peak == 1


@pytest.mark.asyncio
async def test_index_kinds_only_touch_selected_kind(repo: Repository, tmp_path: Path) -> None:
    """只索引封面时不删除已有剧照向量, 之后再补剧照也不会重算封面."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="KND-001",
        poster_urls=["http://example.com/k.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/k1.jpg"]},
    )
    await _seed_image(repo, store, metadata, "http://example.com/k.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/k1.jpg", 20)
    service = _service(repo, store)

    assert (await service.build_index(kinds=[EmbeddingKind.COVER])).indexed == 1
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    assert metadata.id is not None
    assert set((await _embeddings(repo).signatures(model.id, metadata.id))[metadata.id]) == {"http://example.com/k.jpg"}

    assert (await service.build_index(kinds=[EmbeddingKind.STILL])).indexed == 1
    assert set((await _embeddings(repo).signatures(model.id, metadata.id))[metadata.id]) == {
        "http://example.com/k.jpg",
        "http://example.com/k1.jpg",
    }


@pytest.mark.asyncio
async def test_benchmark_measures_without_writing_store(repo: Repository, tmp_path: Path) -> None:
    """性能测试只编码, 不写向量库, 也不要求已建索引."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(
        number="BNC-001",
        poster_urls=["http://example.com/b.jpg"],
        extrafanart_urls={"dmm": ["http://example.com/b1.jpg"]},
    )
    await _seed_image(repo, store, metadata, "http://example.com/b.jpg", 10)
    await _seed_image(repo, store, metadata, "http://example.com/b1.jpg", 20)
    service = _service(repo, store)

    stats = await service.benchmark()

    assert stats.samples == 2
    assert stats.images_per_second > 0
    assert stats.query_ms >= 0
    assert repo.embeddings is not None and not repo.embeddings.exists


@pytest.mark.asyncio
async def test_benchmark_fails_without_model_or_images(repo: Repository, tmp_path: Path) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    service = _service(repo, store)
    service.ready = False
    with pytest.raises(SemanticUnavailable, match="模型尚未下载"):
        await service.benchmark()

    service.ready = True
    with pytest.raises(SemanticUnavailable, match="至少一张"):
        await service.benchmark()


@pytest.mark.asyncio
async def test_query_cache_reuses_one_dot_product(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """翻页、换来源、加筛选都从同一份排名上切, 不再点积."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    for index, width in enumerate((10, 20, 30)):
        metadata = await repo.upsert_metadata(number=f"CAC-{index}", poster_urls=[f"http://example.com/{index}.jpg"])
        await _seed_image(repo, store, metadata, f"http://example.com/{index}.jpg", width)
    service = _service(repo, store)
    await service.build_index()

    calls = 0
    original = service_module.dot

    def counted(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
        nonlocal calls
        calls += 1
        return original(matrix, vector)

    monkeypatch.setattr(service_module, "dot", counted)

    first = await service.search("红色", limit=2)
    second = await service.search("红色", limit=2, offset=2)
    covers = await service.search("红色", limit=2, kind=EmbeddingKind.COVER)
    filtered = await service.search("红色", limit=2, allowed_ids=[first[0].metadata_id])

    assert calls == 1
    assert len(first) + len(second) == 3
    assert {hit.metadata_id for hit in covers} <= {hit.metadata_id for hit in first + second}
    assert [hit.metadata_id for hit in filtered] == [first[0].metadata_id]
    assert await service.count() == 3
    assert calls == 1


@pytest.mark.asyncio
async def test_delete_takes_effect_before_reload(repo: Repository, tmp_path: Path) -> None:
    """删除在写入返回前清位图: 索引还没重装, 结果与总数就已经对得上."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="DEL-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="DEL-002", poster_urls=["http://example.com/2.jpg"])
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    await _seed_image(repo, store, second, "http://example.com/2.jpg", 20)
    service = _service(repo, store)
    await service.build_index()
    assert await service.count() == 2
    assert {hit.metadata_id for hit in await service.search("红色", limit=5)} == {first.id, second.id}
    generation = service._snapshot.index.generation if service._snapshot is not None else None

    assert first.id is not None
    assert await repo.delete_metadata(first.id)

    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [second.id]
    assert await service.count() == 1
    assert service._snapshot is not None and service._snapshot.index.generation == generation


@pytest.mark.asyncio
async def test_search_keeps_old_index_until_reload(repo: Repository, tmp_path: Path) -> None:
    """写入提交后检索仍用旧索引: 不阻塞请求, 也不返回半装好的结果."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="OLD-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="OLD-002", poster_urls=["http://example.com/2.jpg"])
    assert second.id is not None
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()
    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [first.id]

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    await _embeddings(repo).upsert(
        model.id, [EmbeddingWrite(second.id, EmbeddingKind.COVER, "http://example.com/2.jpg", None, _vec([0.5, 0.5]))]
    )

    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [first.id]

    await service._reload_once()
    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [first.id, second.id]


@pytest.mark.asyncio
async def test_background_reload_picks_up_written_vectors(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "RELOAD_QUIET_SECONDS", 0.0)
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="BGR-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="BGR-002", poster_urls=["http://example.com/2.jpg"])
    assert second.id is not None
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()
    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [first.id]

    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    await _embeddings(repo).upsert(
        model.id, [EmbeddingWrite(second.id, EmbeddingKind.COVER, "http://example.com/2.jpg", None, _vec([0.5, 0.5]))]
    )

    for _ in range(200):
        snapshot = service._snapshot
        if snapshot is not None and snapshot.index.generation >= _embeddings(repo).generation:
            break
        await asyncio.sleep(0.005)

    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [first.id, second.id]


@pytest.mark.asyncio
async def test_recreated_id_becomes_visible_after_reload(repo: Repository, tmp_path: Path) -> None:
    """主键没有 AUTOINCREMENT: 删掉最大 id 后新条目会复用同一个 id, 墓碑因此不能只按 id 记."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="REU-001", poster_urls=["http://example.com/1.jpg"])
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()
    assert await service.search("红色", limit=5)
    assert first.id is not None

    assert await repo.delete_metadata(first.id)
    assert await service.search("红色", limit=5) == []

    again = await repo.upsert_metadata(number="REU-002", poster_urls=["http://example.com/2.jpg"])
    assert again.id == first.id
    await _seed_image(repo, store, again, "http://example.com/2.jpg", 20)
    await service.build_index()

    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [again.id]


@pytest.mark.asyncio
async def test_concurrent_cold_reads_load_the_index_once(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="ONE-001", poster_urls=["http://example.com/1.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()

    calls = 0
    original = service.embeddings.load

    async def counted(model_id: int, dim: int) -> EmbeddingIndex:
        nonlocal calls
        calls += 1
        return await original(model_id, dim)

    monkeypatch.setattr(service.embeddings, "load", counted)

    assert await asyncio.gather(*(service.count() for _ in range(5))) == [1] * 5
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_query_shares_one_dot_product(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    for index, width in enumerate((10, 20, 30)):
        metadata = await repo.upsert_metadata(number=f"SHR-{index}", poster_urls=[f"http://example.com/{index}.jpg"])
        await _seed_image(repo, store, metadata, f"http://example.com/{index}.jpg", width)
    service = _service(repo, store)
    await service.build_index()

    calls = 0
    original = service_module.dot

    def counted(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
        nonlocal calls
        calls += 1
        return original(matrix, vector)

    monkeypatch.setattr(service_module, "dot", counted)

    pages = await asyncio.gather(*(service.search("红色", limit=2) for _ in range(4)))

    assert calls == 1
    assert {tuple(hit.metadata_id for hit in page) for page in pages} == {tuple(hit.metadata_id for hit in pages[0])}


def _rows_index(rows: int) -> EmbeddingIndex:
    return EmbeddingIndex(
        model_id=1,
        dim=2,
        generation=0,
        matrix=np.zeros((rows, 2), dtype=np.float32),
        metadata_ids=np.arange(1, rows + 1, dtype=np.int64),
        kinds=np.zeros(rows, dtype=np.uint8),
        urls=tuple(f"u{position}" for position in range(rows)),
    )


@pytest.mark.parametrize(
    ("building", "loaded_ago", "dirty_ago", "rows", "low", "high"),
    [
        # 普通写入: 从最后一次写入算起静默 5 s
        (False, 60.0, 0.0, 0, 4.0, 6.0),
        # 刚装完: 两次重装至少隔 5 s
        (False, 0.0, 0.0, 0, 4.0, 6.0),
        # 持续写入已经超过最长等待: 不再推迟
        (False, 300.0, 40.0, 0, 0.0, 0.5),
        # 全量索引任务期间: 未满 10 分钟
        (True, 100.0, 0.0, 0, 499.0, 501.0),
        # 满 10 分钟但行数没涨 20%
        (True, 700.0, 0.0, 110, 29.0, 31.0),
        # 两个条件都满足
        (True, 700.0, 0.0, 120, 0.0, 0.5),
    ],
)
@pytest.mark.asyncio
async def test_reload_delay_branches(
    repo: Repository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    building: bool,
    loaded_ago: float,
    dirty_ago: float,
    rows: int,
    low: float,
    high: float,
) -> None:
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    service = _service(repo, store)
    service._building = building
    service._dirty_at = time.monotonic() - dirty_ago
    service._dirty_since = service._dirty_at

    async def fake_row_count(model_id: int) -> int:
        return rows

    monkeypatch.setattr(service.embeddings, "row_count", fake_row_count)
    snapshot = service_module._Snapshot(
        index=_rows_index(100), alive=np.ones(100, dtype=bool), loaded_at=time.monotonic() - loaded_ago
    )

    delay = await service._reload_delay(snapshot)

    assert low <= delay <= high


@pytest.mark.asyncio
async def test_build_task_defers_reload_until_settle(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """索引任务期间不按普通静默期重装; 任务结束由 _settle_index 同步补一次."""
    monkeypatch.setattr(service_module, "RELOAD_QUIET_SECONDS", 0.0)
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="BDL-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="BDL-002", poster_urls=["http://example.com/2.jpg"])
    assert second.id is not None
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()
    assert await service.count() == 1

    service._building = True
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    await _embeddings(repo).upsert(
        model.id, [EmbeddingWrite(second.id, EmbeddingKind.COVER, "http://example.com/2.jpg", None, _vec([0.5, 0.5]))]
    )
    for _ in range(20):
        await asyncio.sleep(0.005)
    assert await service.count() == 1

    await service._settle_index()

    assert service._building is False
    assert await service.count() == 2


@pytest.mark.asyncio
async def test_delete_during_cold_load_is_applied(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """冷装载的读事务之后提交的删除, 靠墓碑在装好时摘掉."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="CDL-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="CDL-002", poster_urls=["http://example.com/2.jpg"])
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    await _seed_image(repo, store, second, "http://example.com/2.jpg", 20)
    service = _service(repo, store)
    await service.build_index()
    assert service._snapshot is None

    entered = threading.Event()
    release = threading.Event()
    original = service.embeddings._load_snapshot

    def blocked(model_id: int, dim: int, generation: int) -> EmbeddingIndex:
        index = original(model_id, dim, generation)
        entered.set()
        assert release.wait(timeout=10)
        return index

    monkeypatch.setattr(service.embeddings, "_load_snapshot", blocked)
    pending = asyncio.create_task(service.count())
    assert await asyncio.to_thread(entered.wait, 10)

    assert first.id is not None
    assert await repo.delete_metadata(first.id)
    release.set()

    assert await pending == 1
    assert [hit.metadata_id for hit in await service.search("红色", limit=5)] == [second.id]


@pytest.mark.asyncio
async def test_cold_load_failure_is_not_cached(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读盘失败不能变出一份空索引: 请求报 503, 下次再试."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="CLF-001", poster_urls=["http://example.com/1.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()

    original = service.embeddings._load_snapshot

    def broken(*_args: object) -> EmbeddingIndex:
        raise EmbeddingSnapshotError("disk I/O error")

    monkeypatch.setattr(service.embeddings, "_load_snapshot", broken)
    with pytest.raises(SemanticUnavailable, match="读取失败"):
        await service.count()
    assert service._snapshot is None

    monkeypatch.setattr(service.embeddings, "_load_snapshot", original)

    assert await service.count() == 1


@pytest.mark.asyncio
async def test_reload_read_failure_keeps_the_old_index(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读失败时旧索引继续服务; 故障消失后由下一次读取重新安排重装."""
    monkeypatch.setattr(service_module, "RELOAD_QUIET_SECONDS", 0.0)
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    first = await repo.upsert_metadata(number="RLF-001", poster_urls=["http://example.com/1.jpg"])
    second = await repo.upsert_metadata(number="RLF-002", poster_urls=["http://example.com/2.jpg"])
    assert second.id is not None
    await _seed_image(repo, store, first, "http://example.com/1.jpg", 10)
    service = _service(repo, store)
    await service.build_index()
    assert await service.count() == 1
    before = service._snapshot
    assert before is not None

    original = service.embeddings._load_snapshot

    def broken(*_args: object) -> EmbeddingIndex:
        raise EmbeddingSnapshotError("disk I/O error")

    monkeypatch.setattr(service.embeddings, "_load_snapshot", broken)
    model = await _embeddings(repo).ensure_model("siglip2-base", "v1", DIM)
    await _embeddings(repo).upsert(
        model.id, [EmbeddingWrite(second.id, EmbeddingKind.COVER, "http://example.com/2.jpg", None, _vec([0.5, 0.5]))]
    )
    for _ in range(200):
        if service._reload_retry_at > time.monotonic():
            break
        await asyncio.sleep(0.005)

    assert service._snapshot is before
    assert await service.count() == 1

    monkeypatch.setattr(service.embeddings, "_load_snapshot", original)
    service._reload_retry_at = 0.0
    for _ in range(200):
        await service.count()
        snapshot = service._snapshot
        if snapshot is not None and snapshot.index.generation >= _embeddings(repo).generation:
            break
        await asyncio.sleep(0.005)

    assert await service.count() == 2


@pytest.mark.asyncio
async def test_handoff_moves_index_and_unsubscribes(repo: Repository, tmp_path: Path) -> None:
    """热重载交接后旧服务断开写通知, 删除只作用在新服务的位图上."""
    store = ResourceStore(engine=repo._engine, base_dir=tmp_path / "resources")
    metadata = await repo.upsert_metadata(number="HOF-001", poster_urls=["http://example.com/1.jpg"])
    await _seed_image(repo, store, metadata, "http://example.com/1.jpg", 10)
    old = _service(repo, store, auto_maintain=True)
    await old.build_index()
    assert await old.count() == 1

    new = _service(repo, store, auto_maintain=True)
    new.handoff(old)
    old.close()

    assert new._snapshot is not None
    assert old._snapshot is None
    assert _embeddings(repo)._listeners == [new._on_store_write]

    assert metadata.id is not None
    assert await repo.delete_metadata(metadata.id)

    assert await new.search("红色", limit=5) == []
    assert await new.count() == 0
