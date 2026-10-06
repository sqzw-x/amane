"""清理清单: 遍历产出、覆盖信息与进程内存放."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.library import (
    PLAN_TTL_SECONDS,
    LibraryPlan,
    LibraryScan,
    PlanEntryKind,
    PlanReason,
    PlanSource,
    PlanStore,
    build_plan_tree,
    build_selection_plan,
    find_plan_node,
    new_plan_id,
    scan_plan,
    scan_trash,
)

if TYPE_CHECKING:
    from amane.db.repository import Repository

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _scan(*, blacklist: list[str] | None = None, min_size: int = 0, trailer: str | None = None) -> LibraryScan:
    return LibraryScan(blacklist_patterns=blacklist, min_file_size=min_size, trailer_pattern=trailer)


def _plan(
    lib: Path,
    *,
    scan: LibraryScan | None = None,
    scope: Path | None = None,
    recursive: bool = True,
    limit: int = 20000,
) -> LibraryPlan:
    scope_dir = scope or lib
    return scan_plan.sync(
        scope_dir,
        library_id=1,
        library_root=lib,
        recursive=recursive,
        patterns=[],
        scan=scan or _scan(),
        limit=limit,
    )


def _reasons(plan: LibraryPlan, lib: Path) -> dict[str, PlanReason]:
    return {str(entry.path.relative_to(lib)): entry.reason for entry in plan.entries}


class TestScanPlan:
    def test_classifies_unwanted_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad-1.mkv").write_bytes(b"x" * 10)
        (lib / "work" / "tiny.mp4").write_bytes(b"x")
        (lib / "work" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
        (lib / "work" / "trailer.mp4").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(blacklist=["ad-"], min_size=1024, trailer="trailer"))

        assert _reasons(plan, lib) == {
            "work/ad-1.mkv": PlanReason.BLACKLIST,
            "work/tiny.mp4": PlanReason.UNDERSIZED,
        }
        assert plan.truncated is False
        assert plan.skipped_dirs == 0
        assert plan.skipped_files == 0

    def test_trailer_is_never_undersized(self, tmp_path: Path) -> None:
        """预告片先于体积判定排除: 低码率预告片不是无效文件."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "trailer.mp4").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(min_size=1024, trailer="trailer"))

        assert plan.entries == []

    def test_empty_directory_is_entry(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "empty").mkdir(parents=True)

        plan = _plan(lib)

        assert _reasons(plan, lib) == {"empty": PlanReason.EMPTY_DIR}
        entry = plan.entries[0]
        assert entry.kind is PlanEntryKind.DIR
        assert entry.size is None

    def test_coverage_propagates_upwards(self, tmp_path: Path) -> None:
        """子目录会空时父目录也算会消失: 与自底向上的剪枝一致."""
        lib = tmp_path / "lib"
        (lib / "a" / "b").mkdir(parents=True)
        (lib / "a" / "b" / "tiny.mp4").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(min_size=1024))

        assert plan.dirs[lib / "a"].disk_children == 1
        assert plan.dirs[lib / "a"].will_be_empty is True
        assert plan.dirs[lib / "a" / "b"].will_be_empty is True

    def test_valid_media_keeps_directory(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "a").mkdir(parents=True)
        (lib / "a" / "tiny.mp4").write_bytes(b"x")
        (lib / "a" / "NSFS-039.mp4").write_bytes(b"x" * 4096)

        plan = _plan(lib, scan=_scan(min_size=1024))

        assert plan.dirs[lib / "a"].disk_children == 2
        assert plan.dirs[lib / "a"].will_be_empty is False

    def test_trash_subtree_excluded(self, tmp_path: Path) -> None:
        """回收站整棵不进清单, 且算作不可删除子项: 其父目录不会被预告清除."""
        lib = tmp_path / "lib"
        (lib / "work" / ".amane_trash").mkdir(parents=True)
        (lib / "work" / ".amane_trash" / "old.mkv").write_bytes(b"x")
        (lib / "work" / "ad.mkv").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(blacklist=["ad"]))

        assert _reasons(plan, lib) == {"work/ad.mkv": PlanReason.BLACKLIST}
        assert plan.dirs[lib / "work"].disk_children == 2
        assert plan.dirs[lib / "work"].will_be_empty is False

    def test_directory_holding_only_trash_is_not_empty(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work" / ".amane_trash").mkdir(parents=True)

        plan = _plan(lib)

        assert plan.entries == []

    def test_skips_unreadable_directory(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:  # pragma: no cover - root 无视权限位
            pytest.skip("root 可以读任意目录")
        lib = tmp_path / "lib"
        blocked = lib / "blocked"
        blocked.mkdir(parents=True)
        (blocked / "ad.mkv").write_bytes(b"x")
        blocked.chmod(0)
        try:
            plan = _plan(lib, scan=_scan(blacklist=["ad"]))
        finally:
            blocked.chmod(0o755)

        assert plan.entries == []
        assert plan.skipped_dirs == 1
        assert lib / "blocked" not in plan.dirs

    def test_truncates_at_limit(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        for i in range(5):
            (lib / f"ad-{i}.mkv").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(blacklist=["ad-"]), limit=2)

        assert len(plan.entries) == 2
        assert plan.truncated is True

    def test_non_recursive_ignores_subdirectories(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad.mkv").write_bytes(b"x")
        (lib / "ad-root.mkv").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(blacklist=["ad"]), recursive=False)

        assert _reasons(plan, lib) == {"ad-root.mkv": PlanReason.BLACKLIST}
        assert plan.dirs == {}

    def test_scope_records_subdirectory(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "work").mkdir(parents=True)
        (lib / "work" / "ad.mkv").write_bytes(b"x")
        (lib / "ad.mkv").write_bytes(b"x")

        plan = _plan(lib, scan=_scan(blacklist=["ad"]), scope=lib / "work")

        assert plan.scoped is True
        assert plan.scope_path == lib / "work"
        assert _reasons(plan, lib) == {"work/ad.mkv": PlanReason.BLACKLIST}

    def test_broken_symlink_entry(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "ad.mkv").symlink_to(lib / "nowhere.mkv")

        plan = _plan(lib, scan=_scan(blacklist=["ad"]))

        assert plan.entries[0].kind is PlanEntryKind.SYMLINK

    def test_total_size_dedupes_hardlinks(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        first = lib / "ad-1.mkv"
        second = lib / "ad-2.mkv"
        first.write_bytes(b"x" * 100)
        os.link(first, second)

        plan = _plan(lib, scan=_scan(blacklist=["ad-"]))

        assert len(plan.entries) == 2
        assert plan.total_size == 100


class TestPlanStore:
    def _plan(self, plan_id: str, *, library_id: int = 1, created: datetime = _NOW) -> LibraryPlan:
        return LibraryPlan(
            plan_id=plan_id,
            library_id=library_id,
            root=Path("/lib"),
            scope_path=None,
            recursive=True,
            patterns=(),
            source=PlanSource.RULES,
            created_at=created,
        )

    def test_keeps_recent_window(self) -> None:
        store = PlanStore(keep=2, now=lambda: _NOW)
        for plan_id in ("a", "b", "c"):
            store.put(self._plan(plan_id))

        assert store.get("a") is None
        latest = store.latest(1, PlanSource.RULES)
        assert latest is not None
        assert latest.plan_id == "c"

    def test_expired_plan_is_absent(self) -> None:
        later = _NOW + timedelta(seconds=PLAN_TTL_SECONDS + 1)
        store = PlanStore(now=lambda: later)
        store.put(self._plan("a", created=_NOW))

        assert store.get("a") is None
        assert store.latest(1, PlanSource.RULES) is None

    def test_sources_are_separate(self) -> None:
        store = PlanStore(now=lambda: _NOW)
        rules = self._plan("rules")
        explicit = LibraryPlan(
            plan_id="explicit",
            library_id=1,
            root=rules.root,
            scope_path=None,
            recursive=True,
            patterns=(),
            source=PlanSource.EXPLICIT,
            created_at=_NOW,
        )
        store.put(rules)
        store.put(explicit)

        assert store.latest(1, PlanSource.RULES) is rules
        assert store.latest(1, PlanSource.EXPLICIT) is explicit

    def test_drop_library(self) -> None:
        store = PlanStore(now=lambda: _NOW)
        store.put(self._plan("a", library_id=1))
        store.put(self._plan("b", library_id=2))

        store.drop_library(1)

        assert store.get("a") is None
        assert store.get("b") is not None

    def test_new_plan_id_is_unique(self) -> None:
        assert new_plan_id() != new_plan_id()


class TestPlanTree:
    def test_aggregates_subtree_and_marks_empty(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "a" / "b").mkdir(parents=True)
        (lib / "a" / "b" / "ad-1.mkv").write_bytes(b"x" * 10)
        (lib / "a" / "NSFS-039.mp4").write_bytes(b"x" * 4096)
        plan = _plan(lib, scan=_scan(blacklist=["ad-"]))

        root = build_plan_tree(plan)

        assert root.entry_count == 1
        assert root.entry_bytes == 10
        node_a = find_plan_node(root, lib / "a")
        assert node_a is not None
        assert node_a.entry_count == 1
        assert node_a.will_be_empty is False  # 正片还在
        node_b = find_plan_node(root, lib / "a" / "b")
        assert node_b is not None
        assert node_b.will_be_empty is True
        assert [child.name for child in node_b.children] == ["ad-1.mkv"]
        assert node_b.children[0].reason is PlanReason.BLACKLIST

    def test_hardlink_bytes_counted_once(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        first = lib / "ad-1.mkv"
        second = lib / "ad-2.mkv"
        first.write_bytes(b"x" * 100)
        os.link(first, second)
        plan = _plan(lib, scan=_scan(blacklist=["ad-"]))

        root = build_plan_tree(plan)

        assert root.entry_count == 2
        assert root.entry_bytes == 100
        assert all(child.hardlink for child in root.children)

    def test_empty_dir_entry_is_leaf_node(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "empty").mkdir(parents=True)
        plan = _plan(lib)

        root = build_plan_tree(plan)

        node = root.children[0]
        assert node.is_dir is True
        assert node.reason is PlanReason.EMPTY_DIR
        assert node.will_be_empty is True
        assert node.children == ()

    def test_missing_node_returns_none(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "ad.mkv").write_bytes(b"x")
        plan = _plan(lib, scan=_scan(blacklist=["ad"]))

        root = build_plan_tree(plan)

        assert find_plan_node(root, lib / "gone") is None


class TestScanTrash:
    def test_lists_everything_under_trash(self, tmp_path: Path) -> None:
        """回收站展开不做规则判定: 其下每个文件与空目录都是条目, 回收站目录自身不是."""
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        (trash / "sub").mkdir(parents=True)
        (trash / "old-ad.mp4").write_bytes(b"x" * 10)
        (trash / "sub" / "old-2.mp4").write_bytes(b"x" * 20)
        (trash / "sub" / "empty").mkdir()
        (lib / "keep.mp4").write_bytes(b"x" * 100)

        plan = scan_trash.sync(trash, library_id=1, library_root=lib)

        assert plan.source is PlanSource.EXPLICIT
        assert plan.scope_path == trash
        assert {entry.path.name: entry.reason for entry in plan.entries} == {
            "old-ad.mp4": PlanReason.EXPLICIT,
            "old-2.mp4": PlanReason.EXPLICIT,
            "empty": PlanReason.EXPLICIT,
        }
        assert plan.total_size == 30

    def test_empty_trash_has_no_entries(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        trash = lib / ".amane_trash"
        trash.mkdir(parents=True)

        plan = scan_trash.sync(trash, library_id=1, library_root=lib)

        assert plan.entries == []


class TestSelectionPlan:
    """显式来源展开: 由选中项求作品的完整足迹."""

    async def _seed(self, repo: Repository, tmp_path: Path, *, video_dir_rel: str = "Studio/NSFS-039"):
        from amane.db.models import MediaFileStatus

        root = tmp_path / "lib"
        video_dir = root / video_dir_rel if video_dir_rel else root
        video_dir.mkdir(parents=True, exist_ok=True)
        video = video_dir / "NSFS-039.mp4"
        video.write_bytes(b"v" * 100)
        (video_dir / "NSFS-039.nfo").write_text("nfo")
        (video_dir / "NSFS-039.zh.srt").write_text("sub")
        (video_dir / "cover.jpg").write_bytes(b"cover")
        lib = await repo.create_library(name="t", path=str(root), write_nfo=True)
        assert lib.id is not None
        meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
        assert meta.id is not None
        item = await repo.create_media_file(
            lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )
        return lib, {meta.id: meta}, item, video

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_only_lists_products_and_subtitles(self, repo: Repository, tmp_path: Path) -> None:
        lib, metas, item, video = await self._seed(repo, tmp_path)

        outcome = build_selection_plan(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=False,
        )

        names = sorted(entry.path.name for entry in outcome.plan.entries)
        assert names == ["NSFS-039.mp4", "NSFS-039.nfo", "NSFS-039.zh.srt"]
        assert outcome.notices == []
        assert all(entry.reason is PlanReason.EXPLICIT for entry in outcome.plan.entries)
        assert video.exists()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_lists_everything_inside(self, repo: Repository, tmp_path: Path) -> None:
        """整目录删除按内容展开: 未被索引的同目录文件也在清单里, 用户看得到."""
        lib, metas, item, _video = await self._seed(repo, tmp_path)

        outcome = build_selection_plan(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
        )

        names = sorted(entry.path.name for entry in outcome.plan.entries)
        assert names == ["NSFS-039.mp4", "NSFS-039.nfo", "NSFS-039.zh.srt", "cover.jpg"]
        assert outcome.notices == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_refused_when_sibling_indexed(self, repo: Repository, tmp_path: Path) -> None:
        """目录里还有另一条媒体索引时拒绝整目录删除, 并给出原因."""
        from amane.db.models import MediaFileStatus

        lib, metas, item, video = await self._seed(repo, tmp_path)
        assert lib.id is not None
        sibling = video.parent / "NSFS-040.mp4"
        sibling.write_bytes(b"v" * 100)
        metadata_id = next(iter(metas))
        other = await repo.create_media_file(
            lib.id, path=str(sibling), number="NSFS-040", status=MediaFileStatus.SCRAPED, metadata_id=metadata_id
        )

        outcome = build_selection_plan(
            library=lib,
            items=[item],
            indexed=[item, other],
            metas=metas,
            include_work_dir=True,
        )

        assert len(outcome.notices) == 1
        assert "媒体索引" in outcome.notices[0]
        names = sorted(entry.path.name for entry in outcome.plan.entries)
        assert "cover.jpg" not in names

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_refused_at_library_root(self, repo: Repository, tmp_path: Path) -> None:
        lib, metas, item, _video = await self._seed(repo, tmp_path, video_dir_rel="")

        outcome = build_selection_plan(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
        )

        assert outcome.notices == ["作品目录就是库根, 不提供整目录删除"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_video_is_notice(self, repo: Repository, tmp_path: Path) -> None:
        """选中的目标缺失要提示; 模板产物本来就可能没写过, 不提示."""
        lib, metas, item, video = await self._seed(repo, tmp_path)
        (video.parent / "NSFS-039.nfo").unlink()
        video.unlink()

        outcome = build_selection_plan(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=False,
        )

        assert [entry.path.name for entry in outcome.plan.entries] == ["NSFS-039.zh.srt"]
        assert any("已不在磁盘上" in notice for notice in outcome.notices)
