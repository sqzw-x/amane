"""EmbeddingStore: 幂等注册、覆盖写、流式装载与删除范围 (容器都是临时 SQLite)."""

from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from amane.db.embeddings import (
    EmbeddingIndex,
    EmbeddingKind,
    EmbeddingStore,
    EmbeddingWrite,
    EmbeddingWriteEvent,
    Signatures,
)


def _keys(signatures: Signatures) -> set[tuple[int, str]]:
    return {(metadata_id, url) for metadata_id, rows in signatures.items() for url in rows}


def _vec(*values: float) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncGenerator[EmbeddingStore]:
    instance = EmbeddingStore(tmp_path / "embeddings.db")
    try:
        yield instance
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_ensure_model_is_idempotent(store: EmbeddingStore) -> None:
    first = await store.ensure_model("siglip2-base", "v1", 768)
    second = await store.ensure_model("siglip2-base", "v1", 768)
    assert first == second
    other = await store.ensure_model("siglip2-base", "v2", 768)
    assert other.id != first.id
    assert [(stats.ref.name, stats.ref.version, stats.count) for stats in await store.models()] == [
        ("siglip2-base", "v1", 0),
        ("siglip2-base", "v2", 0),
    ]


@pytest.mark.asyncio
async def test_models_is_empty_without_creating_file(tmp_path: Path) -> None:
    instance = EmbeddingStore(tmp_path / "embeddings.db")
    assert await instance.models() == []
    assert not instance.exists
    await instance.close()


@pytest.mark.asyncio
async def test_upsert_overwrites_same_scope_and_counts_kinds(store: EmbeddingStore) -> None:
    model = await store.ensure_model("siglip2-base", "v1", 4)
    await store.upsert(
        model.id,
        [
            EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", "h1", _vec(1, 0, 0, 0)),
            EmbeddingWrite(1, EmbeddingKind.STILL, "http://a/2.jpg", "h2", _vec(0, 1, 0, 0)),
        ],
    )
    await store.upsert(model.id, [EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", "h1b", _vec(0, 0, 1, 0))])

    stats = (await store.models())[0]
    assert (stats.count, stats.covers, stats.stills) == (2, 1, 1)
    signatures = await store.signatures(model.id)
    assert signatures == {
        1: {"http://a/1.jpg": (EmbeddingKind.COVER, "h1b"), "http://a/2.jpg": (EmbeddingKind.STILL, "h2")}
    }


@pytest.mark.asyncio
async def test_load_returns_preallocated_index_with_kinds(store: EmbeddingStore) -> None:
    model = await store.ensure_model("siglip2-base", "v1", 4)
    await store.upsert(
        model.id,
        [
            EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", "h1", _vec(1, 0, 0, 0)),
            EmbeddingWrite(2, EmbeddingKind.STILL, "http://a/2.jpg", "h2", _vec(0, 1, 0, 0)),
        ],
    )

    index = await store.load(model.id, 4)
    assert isinstance(index, EmbeddingIndex)
    assert index.matrix.shape == (2, 4)
    assert index.metadata_ids.tolist() == [1, 2]
    assert [index.kind_at(position) for position in range(2)] == [EmbeddingKind.COVER, EmbeddingKind.STILL]
    assert index.kinds.dtype == np.uint8
    assert index.entry_ids.tolist() == [1, 2]
    assert index.entry_offsets.tolist() == [0, 1, 2]
    stills = index.kind_layout(EmbeddingKind.STILL)
    assert stills.present.tolist() == [False, True]
    assert stills.entries.tolist() == [1]
    assert index.urls == ("http://a/1.jpg", "http://a/2.jpg")
    assert index.generation == store.generation
    np.testing.assert_array_equal(index.matrix[1], _vec(0, 1, 0, 0))


@pytest.mark.asyncio
async def test_load_groups_rows_by_entry_regardless_of_insert_order(store: EmbeddingStore) -> None:
    """行序由 SQLite 决定, 装载后按 ``(条目 id, 来源)`` 重排下标; 矩阵本身不动."""
    model = await store.ensure_model("siglip2-base", "v1", 4)
    await store.upsert(
        model.id,
        [
            EmbeddingWrite(3, EmbeddingKind.COVER, "u3c", None, _vec(1, 0, 0, 0)),
            EmbeddingWrite(1, EmbeddingKind.STILL, "u1s", None, _vec(0, 1, 0, 0)),
            EmbeddingWrite(3, EmbeddingKind.STILL, "u3s", None, _vec(0, 0, 1, 0)),
            EmbeddingWrite(1, EmbeddingKind.COVER, "u1c", None, _vec(0, 0, 0, 1)),
        ],
    )

    index = await store.load(model.id, 4)

    assert index.metadata_ids.tolist() == [3, 1, 3, 1]
    assert index.urls == ("u3c", "u1s", "u3s", "u1c")
    assert index.row_order.tolist() == [3, 1, 0, 2]
    assert index.entry_ids.tolist() == [1, 3]
    assert index.entry_offsets.tolist() == [0, 2, 4]
    covers = index.kind_layout(EmbeddingKind.COVER)
    assert covers.rows.tolist() == [3, 0]
    assert covers.entries.tolist() == [0, 1]
    assert covers.offsets.tolist() == [0, 1, 2]
    assert covers.present.tolist() == [True, True]
    stills = index.kind_layout(EmbeddingKind.STILL)
    assert stills.present.tolist() == [True, True]


@pytest.mark.asyncio
async def test_generation_advances_on_every_commit(store: EmbeddingStore) -> None:
    """注册模型不算写入; 空操作不通知, 整条删除带出条目 id."""
    events: list[EmbeddingWriteEvent] = []
    store.add_write_listener(events.append)
    assert store.generation == 0

    model = await store.ensure_model("siglip2-base", "v1", 4)
    assert store.generation == 0

    await store.upsert(model.id, [EmbeddingWrite(1, EmbeddingKind.COVER, "u1", None, _vec(1, 0, 0, 0))])
    await store.delete_keys(model.id, [(1, "u1")])
    await store.delete_metadata([2], model_id=model.id)
    assert [event.generation for event in events] == [1, 2]

    await store.upsert(model.id, [EmbeddingWrite(2, EmbeddingKind.COVER, "u2", None, _vec(1, 0, 0, 0))])
    await store.delete_metadata([2])
    assert [event.generation for event in events] == [1, 2, 3, 4]
    assert events[-1].deleted_ids == (2,)


@pytest.mark.asyncio
async def test_load_keeps_the_generation_it_started_with(store: EmbeddingStore) -> None:
    """代次在读事务之前捕获: 装载期间提交的写入留给下一轮重装, 不假装已经包含."""
    model = await store.ensure_model("siglip2-base", "v1", 4)
    await store.upsert(model.id, [EmbeddingWrite(1, EmbeddingKind.COVER, "u1", None, _vec(1, 0, 0, 0))])
    index = await store.load(model.id, 4)
    assert index.generation == 1

    await store.upsert(model.id, [EmbeddingWrite(2, EmbeddingKind.COVER, "u2", None, _vec(0, 1, 0, 0))])

    assert store.generation == 2
    assert index.generation == 1
    assert index.row_count == 1


@pytest.mark.asyncio
async def test_load_skips_vectors_with_wrong_dim(store: EmbeddingStore) -> None:
    model = await store.ensure_model("siglip2-base", "v1", 4)
    await store.upsert(
        model.id,
        [
            EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", "h1", _vec(1, 0, 0, 0)),
            EmbeddingWrite(2, EmbeddingKind.COVER, "http://a/2.jpg", "h2", _vec(1, 0)),
        ],
    )
    index = await store.load(model.id, 4)
    assert index.metadata_ids.tolist() == [1]


@pytest.mark.asyncio
async def test_deletes_are_scoped_by_model_and_metadata(store: EmbeddingStore) -> None:
    model = await store.ensure_model("siglip2-base", "v1", 4)
    other = await store.ensure_model("siglip2-base", "v2", 4)
    rows = [
        EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", "h1", _vec(1, 0, 0, 0)),
        EmbeddingWrite(2, EmbeddingKind.COVER, "http://a/2.jpg", "h2", _vec(1, 0, 0, 0)),
    ]
    await store.upsert(model.id, rows)
    await store.upsert(other.id, rows)

    await store.delete_keys(model.id, [(1, "http://a/1.jpg")])
    assert _keys(await store.signatures(model.id)) == {(2, "http://a/2.jpg")}
    assert len(_keys(await store.signatures(other.id))) == 2

    await store.delete_metadata([2], model_id=model.id)
    assert await store.signatures(model.id) == {}
    assert len(_keys(await store.signatures(other.id))) == 2

    # 不带 model_id 时连其它模型的同一批条目一起删, 供级联删除使用.
    await store.upsert(model.id, rows)
    await store.delete_metadata([2])
    assert _keys(await store.signatures(model.id)) == {(1, "http://a/1.jpg")}
    assert _keys(await store.signatures(other.id)) == {(1, "http://a/1.jpg")}


@pytest.mark.asyncio
async def test_signature_changes_after_write(store: EmbeddingStore) -> None:
    model = await store.ensure_model("siglip2-base", "v1", 4)
    assert await store.signature(model.id) == (0, None)
    await store.upsert(model.id, [EmbeddingWrite(1, EmbeddingKind.COVER, "http://a/1.jpg", None, _vec(1, 0, 0, 0))])
    count, last = await store.signature(model.id)
    assert count == 1 and last is not None


@pytest.mark.asyncio
async def test_write_listener_errors_do_not_break_writes(store: EmbeddingStore) -> None:
    """写入已经提交: 监听者的毛病不能变成写入方的失败, 也不能挡住后面的监听者."""
    model = await store.ensure_model("siglip2-base", "v1", 4)
    seen: list[int] = []

    def broken(event: EmbeddingWriteEvent) -> None:
        raise RuntimeError("listener bug")

    store.add_write_listener(broken)
    store.add_write_listener(lambda event: seen.append(event.generation))

    await store.upsert(model.id, [EmbeddingWrite(1, EmbeddingKind.COVER, "u1", None, _vec(1, 0, 0, 0))])
    await store.delete_metadata([1])

    assert seen == [1, 2]
    assert await store.signatures(model.id) == {}
