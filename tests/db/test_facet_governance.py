"""分类治理的写锁与投影一致性: 读改写同事务, 真值与投影逐行对应.

并发用例用事件固定交错时序: 读改写在读到行之后暂停, 并发写入必须等写锁释放才能提交.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from pytest import MonkeyPatch
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.db.models import FacetKind, FacetRule, FacetRuleAction, Metadata
from amane.db.repo_types import FacetItem, WriteMode
from amane.db.repos.facet_helpers import LINK_FACETS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio


async def _facet_id(repo: Repository, kind: FacetKind, name: str) -> int:
    items, _ = await repo.list_facets(kind, search=name)
    facet_id = next(item.id for item in items if item.name == name)
    assert facet_id is not None
    return facet_id


async def _projection(repo: Repository, kind: FacetKind, metadata_id: int) -> list[tuple[int, str]]:
    """关联行按 position 排序的 (position, 名字)."""
    spec = LINK_FACETS[kind]
    async with repo._session() as session:
        rows = (
            await session.exec(
                select(spec.link_position, col(spec.entity.name))
                .join(spec.entity, col(spec.link_fk) == col(spec.entity.id))
                .where(col(spec.link.metadata_id) == metadata_id)
                .order_by(spec.link_position)
            )
        ).all()
    return [(int(position), str(name)) for position, name in rows]


def _expected(names: Sequence[str]) -> list[tuple[int, str]]:
    return [(position, name) for position, name in enumerate(names)]


class TestRenameProjection:
    """改名只动真值列; 规则让名字增删或换实体时才重建投影."""

    async def test_rename_updates_truth_and_keeps_positions(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="GOV-RN-1", tags=["hot", "b", "c"])
        assert meta.id is not None
        facet_id = await _facet_id(repo, FacetKind.TAG, "hot")

        renamed = await repo.rename_facet(FacetKind.TAG, facet_id, "renamed")

        assert renamed is not None and renamed.count == 1
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == ["renamed", "b", "c"]
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(["renamed", "b", "c"])

    async def test_rename_to_blocked_name_drops_name(self, repo: Repository) -> None:
        """新名字命中 block: 真值里名字消失, 投影不得留下指向该实体的行."""
        await repo.upsert_metadata(number="GOV-BLK-1", tags=["Doomed"])
        doomed_id = await _facet_id(repo, FacetKind.TAG, "Doomed")
        assert await repo.delete_facet(FacetKind.TAG, doomed_id) is True

        meta = await repo.upsert_metadata(number="GOV-BLK-2", tags=["Old", "Keep"])
        assert meta.id is not None
        old_id = await _facet_id(repo, FacetKind.TAG, "Old")

        renamed = await repo.rename_facet(FacetKind.TAG, old_id, "Doomed")

        assert renamed is not None and renamed.count == 0
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == ["Keep"]
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(["Keep"])

    async def test_rename_to_aliased_name_follows_rule(self, repo: Repository) -> None:
        """新名字已是 alias: 真值落到别名目标, 投影必须指向目标实体而不是被改名的实体."""
        await repo.upsert_metadata(number="GOV-AL-1", tags=["Aliased"])
        aliased_id = await _facet_id(repo, FacetKind.TAG, "Aliased")
        assert await repo.rename_facet(FacetKind.TAG, aliased_id, "Canonical") is not None
        canonical_id = await _facet_id(repo, FacetKind.TAG, "Canonical")

        meta = await repo.upsert_metadata(number="GOV-AL-2", tags=["Old", "Keep"])
        assert meta.id is not None
        old_id = await _facet_id(repo, FacetKind.TAG, "Old")

        renamed = await repo.rename_facet(FacetKind.TAG, old_id, "Aliased")

        assert renamed is not None and renamed.count == 0
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == ["Canonical", "Keep"]
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(["Canonical", "Keep"])
        assert await repo.get_facet(FacetKind.TAG, canonical_id) is not None

    @pytest.mark.parametrize("kind", [FacetKind.TAG, FacetKind.DIRECTOR])
    @pytest.mark.parametrize(
        "other_action",
        [None, FacetRuleAction.ALIAS, FacetRuleAction.BLOCK],
        ids=["unchanged", "scalar-alias", "actor-block"],
    )
    async def test_rename_back_rebuilds_unchanged_truth(
        self,
        repo: Repository,
        kind: FacetKind,
        other_action: FacetRuleAction | None,
    ) -> None:
        meta = await repo.upsert_metadata(
            number="GOV-RN-BACK",
            tags=["Keep", "Original"] if kind == FacetKind.TAG else [],
            directors=["Keep", "Original"] if kind == FacetKind.DIRECTOR else [],
            actors=["OldActor"],
            studio="OldStudio",
        )
        assert meta.id is not None
        facet_id = await _facet_id(repo, kind, "Original")
        renamed = await repo.rename_facet(kind, facet_id, "Renamed")
        assert renamed is not None and renamed.count == 1
        before = await repo.get_metadata(meta.id)
        assert before is not None
        if other_action is not None:
            async with repo._session() as session:
                session.add(
                    FacetRule(
                        kind=FacetKind.STUDIO if other_action == FacetRuleAction.ALIAS else FacetKind.ACTOR,
                        source_name="OldStudio" if other_action == FacetRuleAction.ALIAS else "OldActor",
                        action=other_action,
                        target_name="NewStudio" if other_action == FacetRuleAction.ALIAS else None,
                    )
                )
                await session.commit()

        restored = await repo.rename_facet(kind, facet_id, "Original")

        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == before.tags
        assert got.directors == before.directors
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(got.tags)
        assert await _projection(repo, FacetKind.DIRECTOR, meta.id) == _expected(got.directors)
        assert restored is not None and restored.name == "Original" and restored.count == 0
        canonical = await repo.get_facet(kind, await _facet_id(repo, kind, "Renamed"))
        assert canonical is not None and canonical.id != facet_id and canonical.count == 1
        assert got.actors == ([] if other_action == FacetRuleAction.BLOCK else ["OldActor"])
        assert await _projection(repo, FacetKind.ACTOR, meta.id) == _expected(got.actors)
        assert got.studio == ("NewStudio" if other_action == FacetRuleAction.ALIAS else "OldStudio")
        if other_action is None:
            assert got.updated_at == before.updated_at
        rules = await repo.list_facet_rules(kind)
        assert [(rule.source_name, rule.action, rule.target_name) for rule in rules] == [
            ("Original", FacetRuleAction.ALIAS, "Renamed")
        ]

    async def test_rename_batch_does_not_rebuild_untouched_rows(self, repo: Repository) -> None:
        """一批影片共享同一标签: 改名后每行的真值与投影都逐项对应."""
        metas = [await repo.upsert_metadata(number=f"GOV-RN-B{i}", tags=["hot", f"t{i}"]) for i in range(20)]
        facet_id = await _facet_id(repo, FacetKind.TAG, "hot")

        assert await repo.rename_facet(FacetKind.TAG, facet_id, "renamed") is not None

        for index, meta in enumerate(metas):
            assert meta.id is not None
            got = await repo.get_metadata(meta.id)
            assert got is not None
            assert got.tags == ["renamed", f"t{index}"]
            assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(["renamed", f"t{index}"])


class TestMergeProjection:
    """合并去重后 position 必须等于最终真值列表的下标."""

    @pytest.mark.parametrize(
        ("source_tags", "expected"),
        [
            (["A", "B", "Other"], ["A", "Other"]),
            (["B", "A", "Other"], ["A", "Other"]),
            (["Other", "B"], ["Other", "A"]),
            (["Other", "B", "A"], ["Other", "A"]),
            (["B", "A"], ["A"]),
            (["A", "Other"], ["A", "Other"]),
            # 目标名已在真值里: 去重后既有行保留, 源行删除
            (["A", "B", "C"], ["A", "C"]),
        ],
    )
    async def test_merge_keeps_position_order(
        self, repo: Repository, source_tags: list[str], expected: list[str]
    ) -> None:
        meta = await repo.upsert_metadata(number="GOV-MG-1", tags=source_tags)
        await repo.upsert_metadata(number="GOV-MG-2", tags=["A", "B"])
        assert meta.id is not None
        target_id = await _facet_id(repo, FacetKind.TAG, "A")
        source_id = await _facet_id(repo, FacetKind.TAG, "B")

        merged = await repo.merge_facets(FacetKind.TAG, target_id, [source_id])

        assert merged is not None
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == expected
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(expected)

    @pytest.mark.parametrize("source_count", [500, 501])
    async def test_merge_overlapping_chunks_with_other_kind_rule(self, repo: Repository, source_count: int) -> None:
        meta = await repo.upsert_metadata(
            number="GOV-MG-CHUNKS",
            tags=[f"Source-{index}" for index in range(source_count)],
            directors=["OldDirector", "KeepDirector"],
        )
        target_meta = await repo.upsert_metadata(number="GOV-MG-TARGET", tags=["Target"])
        assert meta.id is not None and target_meta.id is not None
        target_id = await _facet_id(repo, FacetKind.TAG, "Target")
        sources, _ = await repo.list_facets(FacetKind.TAG, search="Source-", limit=source_count)
        assert len(sources) == source_count
        async with repo._session() as session:
            session.add(
                FacetRule(
                    kind=FacetKind.DIRECTOR,
                    source_name="OldDirector",
                    action=FacetRuleAction.ALIAS,
                    target_name="NewDirector",
                )
            )
            await session.commit()

        merged = await repo.merge_facets(FacetKind.TAG, target_id, [source.id for source in sources])

        assert merged is not None and merged.count == 2
        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == ["Target"]
        assert got.directors == ["NewDirector", "KeepDirector"]
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(got.tags)
        assert await _projection(repo, FacetKind.DIRECTOR, meta.id) == _expected(got.directors)
        assert await _projection(repo, FacetKind.TAG, target_meta.id) == _expected(["Target"])
        assert await repo.list_facets(FacetKind.TAG, search="Source-") == ([], 0)

    async def test_merge_actor_keeps_position_order(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="GOV-MGA-1", actors=["B", "A", "Other"])
        assert meta.id is not None
        target_id = await _facet_id(repo, FacetKind.ACTOR, "A")
        source_id = await _facet_id(repo, FacetKind.ACTOR, "B")

        assert await repo.merge_facets(FacetKind.ACTOR, target_id, [source_id]) is not None

        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.actors == ["A", "Other"]
        assert await _projection(repo, FacetKind.ACTOR, meta.id) == _expected(["A", "Other"])


class TestDeleteProjection:
    async def test_delete_strips_names_from_truth_and_projection(self, repo: Repository) -> None:
        meta = await repo.upsert_metadata(number="GOV-DEL-1", tags=["Doomed", "Keep"])
        assert meta.id is not None
        doomed_id = await _facet_id(repo, FacetKind.TAG, "Doomed")

        assert await repo.delete_facet(FacetKind.TAG, doomed_id) is True

        got = await repo.get_metadata(meta.id)
        assert got is not None
        assert got.tags == ["Keep"]
        assert await _projection(repo, FacetKind.TAG, meta.id) == _expected(["Keep"])


class TestGovernanceConcurrency:
    """两个写入方按固定交错执行: 写锁未释放时并发写入不得提交."""

    async def test_update_metadata_and_upsert_interleaved(self, repo: Repository, monkeypatch: MonkeyPatch) -> None:
        """PATCH 读到行后暂停, 并发 upsert 必须等它提交; 投影与真值保持一致."""
        meta = await repo.upsert_metadata(number="RACE-001", tags=["t1"], title="old")
        assert meta.id is not None
        metadata_id = meta.id

        observed, release = await _pause_on_metadata_get(monkeypatch, metadata_id)
        patched = asyncio.create_task(repo.update_metadata(metadata_id, mode=WriteMode.MANUAL, title="patched"))
        try:
            await asyncio.wait_for(observed.wait(), timeout=10)
            concurrent = asyncio.create_task(repo.upsert_metadata(number="RACE-001", tags=["t2"]))
            await asyncio.sleep(0.2)
            assert not concurrent.done(), "读改写尚未提交, 并发写入不得越过写锁"
            release.set()
            await asyncio.wait_for(patched, timeout=10)
            await asyncio.wait_for(concurrent, timeout=10)
        finally:
            release.set()

        got = await repo.get_metadata(metadata_id)
        assert got is not None
        assert got.title == "patched"
        assert got.tags == ["t2"]
        assert await _projection(repo, FacetKind.TAG, metadata_id) == _expected(["t2"])

    async def test_merge_entries_does_not_write_back_stale_raw(
        self, repo: Repository, monkeypatch: MonkeyPatch
    ) -> None:
        """合并读到目标行后暂停; 并发写入的新 raw 不得被合并按旧快照整体覆盖."""
        target = await repo.upsert_metadata(number="RACE-RAW-1", raw={"javdb": {"title": "t"}})
        source = await repo.upsert_metadata(number="RACE-RAW-2", raw={"dmm": {"title": "s"}})
        assert target.id is not None and source.id is not None

        observed, release = await _pause_on_metadata_get(monkeypatch, target.id)
        merged = asyncio.create_task(repo.merge_metadata_entries(target.id, [source.id]))
        try:
            await asyncio.wait_for(observed.wait(), timeout=10)
            concurrent = asyncio.create_task(
                repo.upsert_metadata(number="RACE-RAW-1", raw={"extra": {"title": "live"}})
            )
            await asyncio.sleep(0.2)
            assert not concurrent.done(), "合并尚未提交, 并发写入不得越过写锁"
            release.set()
            await asyncio.wait_for(merged, timeout=10)
            await asyncio.wait_for(concurrent, timeout=10)
        finally:
            release.set()

        got = await repo.get_metadata(target.id)
        assert got is not None
        assert got.raw == {"extra": {"title": "live"}}

    async def test_concurrent_rename_same_name_has_single_winner(self, repo: Repository) -> None:
        """并发同名改名: 判定与写入同事务, 后到者看到已提交的名字并报冲突."""
        await repo.upsert_metadata(number="RACE-RN-1", tags=["RaceA"])
        await repo.upsert_metadata(number="RACE-RN-2", tags=["RaceB"])
        first = await _facet_id(repo, FacetKind.TAG, "RaceA")
        second = await _facet_id(repo, FacetKind.TAG, "RaceB")

        results = await asyncio.gather(
            repo.rename_facet(FacetKind.TAG, first, "RaceSame"),
            repo.rename_facet(FacetKind.TAG, second, "RaceSame"),
            return_exceptions=True,
        )

        winners = [result for result in results if isinstance(result, FacetItem)]
        conflicts = [result for result in results if isinstance(result, ValueError)]
        assert len(winners) == 1
        assert len(conflicts) == 1
        assert "合并" in str(conflicts[0])
        renamed_ids = [item.id for item in winners]
        assert await repo.get_facet(FacetKind.TAG, renamed_ids[0]) is not None


async def _pause_on_metadata_get(monkeypatch: MonkeyPatch, metadata_id: int) -> tuple[asyncio.Event, asyncio.Event]:
    """让 ``AsyncSession.get(Metadata, metadata_id)`` 返回后暂停一次, 返回 (已暂停, 放行)."""
    observed, release = asyncio.Event(), asyncio.Event()
    original = AsyncSession.get
    armed = True

    async def paused_get(self: AsyncSession, entity: type, ident: object, **kwargs: Any) -> Any:
        nonlocal armed
        obj = await original(self, entity, ident, **kwargs)
        if armed and entity is Metadata and ident == metadata_id:
            armed = False
            observed.set()
            await release.wait()
        return obj

    monkeypatch.setattr(AsyncSession, "get", paused_get)
    return observed, release
