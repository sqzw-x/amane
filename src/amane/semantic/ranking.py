"""语义排名: 图片级点积、条目级聚合与稳定排序.

排序结果在每次查询中只计算一次: 任意筛选、翻页与来源均取自同一份条目排名, 因此翻页不会重复
计算, 也不会出现同分顺序漂移. 同分规则固定为 ``(分数降序, 条目 id 升序)``: 分数来自 float32 点积,
同分很常见, 没有显式次序时翻页会跳条目.

这里的函数都是纯数组运算, 由调用方放进线程执行 (见 :meth:`amane.semantic.service.SemanticService.search`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..db.embeddings import EmbeddingIndex, EmbeddingKind


@dataclass(frozen=True)
class EntryRanking:
    """一次查询下全部条目的分数与次序; 只对数组运算, 与筛选、来源无关."""

    scores: np.ndarray
    """``(条目数,)`` float32; 该来源没有图的条目为 ``-inf``."""
    order: np.ndarray
    """``(条目数,)`` int32, 条目槽位按 ``(分数降序, 条目 id 升序)`` 排列."""


def dot(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """整矩阵点积; 矩阵只读, 由 BLAS 多线程执行."""
    return matrix @ vector


def rank_entries(index: EmbeddingIndex, scores: np.ndarray, kind: EmbeddingKind | None) -> EntryRanking:
    """每条目取该来源的最高分图, 再排出全部条目.

    只对请求的来源聚合: 三种来源各执行一遍是三次 ``reduceat`` 加三次全量排序, 而一次查询通常只使用一种.
    """
    entry_scores = np.full(index.entry_count, -np.inf, dtype=np.float32)
    if index.entry_count:
        if kind is None:
            grouped = scores[index.row_order]
            entry_scores = np.maximum.reduceat(grouped, index.entry_offsets[:-1])
        else:
            layout = index.kind_layout(kind)
            if layout.rows.size:
                entry_scores[layout.entries] = np.maximum.reduceat(scores[layout.rows], layout.offsets[:-1])
    order = np.lexsort((index.entry_ids, -entry_scores)).astype(np.int32)
    return EntryRanking(scores=entry_scores, order=order)


def best_row(index: EmbeddingIndex, scores: np.ndarray, kind: EmbeddingKind | None, slot: int) -> int:
    """条目里该来源分数最高的那张图; 只在出页的条目上调用."""
    if kind is None:
        start, end = int(index.entry_offsets[slot]), int(index.entry_offsets[slot + 1])
        rows = index.row_order[start:end]
    else:
        layout = index.kind_layout(kind)
        position = int(np.searchsorted(layout.entries, slot))
        start, end = int(layout.offsets[position]), int(layout.offsets[position + 1])
        rows = layout.rows[start:end]
    return int(rows[int(np.argmax(scores[rows]))])
