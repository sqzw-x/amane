"""条目级排名: 同片多图聚合、来源过滤、同分次序与最佳图回查."""

from typing import TYPE_CHECKING

import numpy as np
import pytest

from amane.db.embeddings import EmbeddingIndex, EmbeddingKind
from amane.semantic.ranking import best_row, rank_entries

if TYPE_CHECKING:
    from collections.abc import Sequence

COVER = EmbeddingKind.COVER
STILL = EmbeddingKind.STILL


def _index(rows: Sequence[tuple[int, EmbeddingKind, float]]) -> EmbeddingIndex:
    return EmbeddingIndex(
        model_id=1,
        dim=2,
        generation=0,
        matrix=np.asarray([[score, 0.0] for _, _, score in rows], dtype=np.float32),
        metadata_ids=np.asarray([metadata_id for metadata_id, _, _ in rows], dtype=np.int64),
        kinds=np.asarray([0 if kind is COVER else 1 for _, kind, _ in rows], dtype=np.uint8),
        urls=tuple(f"u{position}" for position in range(len(rows))),
    )


def _scores(index: EmbeddingIndex) -> np.ndarray:
    return index.matrix @ np.asarray([1.0, 0.0], dtype=np.float32)


def test_entries_take_the_best_image_of_each_kind() -> None:
    index = _index([(2, COVER, 0.9), (1, STILL, 0.8), (2, STILL, 0.95), (1, COVER, 0.7)])
    scores = _scores(index)

    # 槽位按条目 id 升序: 0 是条目 1, 1 是条目 2.
    everything = rank_entries(index, scores, None)
    assert everything.order.tolist() == [1, 0]
    assert everything.scores.tolist() == pytest.approx([0.8, 0.95])

    covers = rank_entries(index, scores, COVER)
    assert covers.order.tolist() == [1, 0]
    assert covers.scores.tolist() == pytest.approx([0.7, 0.9])

    stills = rank_entries(index, scores, STILL)
    assert stills.order.tolist() == [1, 0]
    assert stills.scores.tolist() == pytest.approx([0.8, 0.95])


def test_kind_filter_excludes_entries_without_that_kind() -> None:
    """没有剧照的条目在剧照排名里是 -inf, 排序落在最后, 出页前还要按 present 屏蔽."""
    index = _index([(1, COVER, 0.6), (2, COVER, 0.5), (2, STILL, 0.4)])
    scores = _scores(index)

    stills = rank_entries(index, scores, STILL)
    assert stills.scores.tolist()[1] == pytest.approx(0.4)
    assert np.isneginf(stills.scores[0])
    assert stills.order.tolist() == [1, 0]
    assert index.kind_layout(STILL).present.tolist() == [False, True]


def test_equal_scores_fall_back_to_metadata_id() -> None:
    """同分常见 (float32 点积), 没有固定次序时翻页会跳条目."""
    index = _index([(3, COVER, 0.5), (1, COVER, 0.5), (2, COVER, 0.5)])
    ranking = rank_entries(index, _scores(index), None)
    assert index.entry_ids[ranking.order].tolist() == [1, 2, 3]


def test_best_row_respects_the_requested_kind() -> None:
    index = _index([(2, COVER, 0.9), (1, STILL, 0.8), (2, STILL, 0.95), (1, COVER, 0.7)])
    scores = _scores(index)
    slot = index.entry_slot(2)
    assert slot is not None

    assert index.urls[best_row(index, scores, None, slot)] == "u2"
    assert index.urls[best_row(index, scores, STILL, slot)] == "u2"
    assert index.urls[best_row(index, scores, COVER, slot)] == "u0"


def test_empty_index_ranks_nothing() -> None:
    index = _index([])
    scores = np.zeros(0, dtype=np.float32)
    for kind in (None, COVER, STILL):
        ranking = rank_entries(index, scores, kind)
        assert ranking.order.size == 0
        assert ranking.scores.size == 0
