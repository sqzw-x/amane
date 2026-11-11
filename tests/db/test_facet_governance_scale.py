"""万级关联下的分类治理: 批量写入与投影一致性.

逐部影片重建投影会让 SQL 语句数随影片数增长; 这里用语句数而不是墙钟时间做回归判据.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, insert
from sqlmodel import col, select

from amane.db.models import FacetKind, Metadata, MetadataTag, Tag

if TYPE_CHECKING:
    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio

#: 关联同一标签的影片数.
FILMS = 10_000
#: 单次治理允许的语句数上限; 逐部重建会随影片数增长, 远超此值.
MAX_GOVERNANCE_STATEMENTS = 500


class _Statements:
    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(
        self, _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool
    ) -> None:
        self.items.append(statement)


@contextmanager
def _capture_statements(repo: Repository) -> Iterator[_Statements]:
    captured = _Statements()
    engine = repo._engine.sync_engine
    event.listen(engine, "before_cursor_execute", captured)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", captured)


async def _seed_shared_tag(repo: Repository) -> tuple[int, int]:
    """建 10,000 部影片: 全部挂 ``hot``, 同时全部挂 ``shared`` (合并时必然要去重).

    直接写表而不走 ``upsert_metadata``: 逐条投影重建不是被测对象, 也不该拖慢用例.
    """
    async with repo._session() as session:
        connection = await session.connection()
        now = datetime.now(UTC)
        await connection.execute(
            insert(Metadata),
            [
                {
                    "number": f"SCALE-{index:05d}",
                    "tags": ["hot", f"t{index % 50}", "shared"]
                    if index % 2 == 0
                    else [f"t{index % 50}", "hot", "shared"],
                    "actors": [],
                    "directors": [],
                    "created_at": now,
                    "updated_at": now,
                }
                for index in range(FILMS)
            ],
        )
        await connection.execute(
            insert(Tag), [{"name": name} for name in ["hot", "shared", *(f"t{index}" for index in range(50))]]
        )
        tag_ids = {
            str(name): int(tag_id)
            for tag_id, name in (await session.exec(select(Tag.id, Tag.name))).all()
            if tag_id is not None
        }
        await connection.execute(
            insert(MetadataTag),
            [
                {"metadata_id": metadata_id, "tag_id": tag_ids[name], "position": position}
                for metadata_id, tags in (await session.exec(select(col(Metadata.id), col(Metadata.tags)))).all()
                for position, name in enumerate(tags)
            ],
        )
        await session.commit()
    return tag_ids["hot"], tag_ids["shared"]


async def _assert_projection_matches_truth(repo: Repository) -> None:
    """逐部影片断言: 投影按 position 排序后的名字序列等于真值列表, 且 position 等于下标."""
    async with repo._session() as session:
        truth = {
            int(metadata_id): [str(name) for name in tags]
            for metadata_id, tags in (await session.exec(select(col(Metadata.id), col(Metadata.tags)))).all()
            if metadata_id is not None
        }
        rows = (
            await session.exec(
                select(col(MetadataTag.metadata_id), col(MetadataTag.position), col(Tag.name))
                .join(Tag, col(MetadataTag.tag_id) == col(Tag.id))
                .order_by(col(MetadataTag.metadata_id), col(MetadataTag.position))
            )
        ).all()
    projected: dict[int, list[tuple[int, str]]] = {}
    for metadata_id, position, name in rows:
        projected.setdefault(int(metadata_id), []).append((int(position), str(name)))
    mismatched = {
        metadata_id: (names, projected.get(metadata_id, []))
        for metadata_id, names in truth.items()
        if projected.get(metadata_id, []) != list(enumerate(names))
    }
    assert not mismatched, f"{len(mismatched)} 部影片的投影与真值不一致, 例如 {list(mismatched.items())[:1]}"


class TestLargeFacetGovernance:
    async def test_rename_then_merge_ten_thousand_links(self, repo: Repository) -> None:
        hot_id, shared_id = await _seed_shared_tag(repo)
        await _assert_projection_matches_truth(repo)

        with _capture_statements(repo) as renamed_statements:
            renamed = await repo.rename_facet(FacetKind.TAG, hot_id, "hot-renamed")
        assert renamed is not None and renamed.count == FILMS
        assert len(renamed_statements.items) < MAX_GOVERNANCE_STATEMENTS
        await _assert_projection_matches_truth(repo)

        with _capture_statements(repo) as merged_statements:
            merged = await repo.merge_facets(FacetKind.TAG, shared_id, [renamed.id])
        # 每部影片同时挂源与目标: 去重后每行的标签数减 1.
        assert merged is not None and merged.count == FILMS
        assert len(merged_statements.items) < MAX_GOVERNANCE_STATEMENTS
        await _assert_projection_matches_truth(repo)

    async def test_delete_ten_thousand_links(self, repo: Repository) -> None:
        hot_id, _shared_id = await _seed_shared_tag(repo)

        with _capture_statements(repo) as delete_statements:
            assert await repo.delete_facet(FacetKind.TAG, hot_id) is True
        assert len(delete_statements.items) < MAX_GOVERNANCE_STATEMENTS

        await _assert_projection_matches_truth(repo)
