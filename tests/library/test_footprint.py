"""显式来源展开: 由选中项求作品的完整足迹."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.library import FootprintNotice, FootprintNoticeKind, InventoryEntryKind, InventoryReason, build_footprint
from amane.library.cleanup import footprint as footprint_module

if TYPE_CHECKING:
    from amane.db.repository import Repository


class TestFootprint:
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

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=False,
        )

        names = sorted(entry.path.name for entry in outcome.inventory.entries)
        assert names == ["NSFS-039.mp4", "NSFS-039.nfo", "NSFS-039.zh.srt"]
        assert outcome.notices == []
        assert all(entry.reason is InventoryReason.EXPLICIT for entry in outcome.inventory.entries)
        assert video.exists()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_lists_everything_inside(self, repo: Repository, tmp_path: Path) -> None:
        """整目录删除按内容展开: 未被索引的同目录文件也在清单里, 用户看得到."""
        lib, metas, item, _video = await self._seed(repo, tmp_path)

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
        )

        names = sorted(entry.path.name for entry in outcome.inventory.entries)
        assert names == ["NSFS-039.mp4", "NSFS-039.nfo", "NSFS-039.zh.srt", "cover.jpg"]
        assert outcome.notices == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_includes_empty_subdirectory(self, repo: Repository, tmp_path: Path) -> None:
        """空子目录自身也是条目: 否则「删除所在目录」会因为残留的空目录而不成立."""
        lib, metas, item, video = await self._seed(repo, tmp_path)
        (video.parent / "empty").mkdir()

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
        )

        kinds = {entry.path.name: entry.kind for entry in outcome.inventory.entries}
        assert kinds["empty"] is InventoryEntryKind.DIR

    @pytest.mark.asyncio(loop_scope="function")
    async def test_truncation_is_visible(self, repo: Repository, tmp_path: Path) -> None:
        """选中项触顶要标记已截断并说明还有多少没纳入, 否则用户以为整份足迹都在清单里."""
        lib, metas, item, _video = await self._seed(repo, tmp_path)

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
            limit=1,
        )

        assert len(outcome.inventory.entries) == 1
        assert outcome.inventory.truncated is True
        assert outcome.inventory.dropped == 3
        # 截断不另发提示: 面板按 truncated / dropped 自己给出文案.
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

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item, other],
            metas=metas,
            include_work_dir=True,
        )

        assert outcome.notices == [FootprintNotice(FootprintNoticeKind.WORK_DIR_MULTIPLE, path=video.parent, count=2)]
        names = sorted(entry.path.name for entry in outcome.inventory.entries)
        assert "cover.jpg" not in names

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_refused_at_library_root(self, repo: Repository, tmp_path: Path) -> None:
        lib, metas, item, _video = await self._seed(repo, tmp_path, video_dir_rel="")

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=True,
        )

        assert outcome.notices == [FootprintNotice(FootprintNoticeKind.WORK_DIR_IS_ROOT)]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_video_is_notice(self, repo: Repository, tmp_path: Path) -> None:
        """选中的目标缺失要提示; 模板产物本来就可能没写过, 不提示."""
        lib, metas, item, video = await self._seed(repo, tmp_path)
        (video.parent / "NSFS-039.nfo").unlink()
        video.unlink()

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=False,
        )

        assert [entry.path.name for entry in outcome.inventory.entries] == ["NSFS-039.zh.srt"]
        assert any(notice.kind is FootprintNoticeKind.MISSING for notice in outcome.notices)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unstattable_video_is_notice(
        self, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """存在性检查与 stat 之间文件消失 (或挂载盘掉线) 只记一条提示, 不让整次展开失败."""
        lib, metas, item, video = await self._seed(repo, tmp_path)
        lstat = Path.lstat

        def statless(self: Path) -> os.stat_result:
            if self == video:
                raise OSError("mount gone")
            return lstat(self)

        monkeypatch.setattr(Path, "lstat", statless)

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas=metas,
            include_work_dir=False,
        )

        assert [entry.path.name for entry in outcome.inventory.entries] == ["NSFS-039.nfo", "NSFS-039.zh.srt"]
        assert any(notice.kind is FootprintNoticeKind.MISSING for notice in outcome.notices)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_outside_products_left_alone(self, repo: Repository, tmp_path: Path) -> None:
        """链接模式: 面向媒体服务器的那一份写在库外链接树, 清单只收库根内的路径."""
        from amane.db.models import MediaFileStatus

        root = tmp_path / "lib"
        video_dir = root / "Studio" / "NSFS-039"
        video_dir.mkdir(parents=True)
        video = video_dir / "NSFS-039.mp4"
        video.write_bytes(b"v" * 100)
        link_tree = tmp_path / "linktree" / "NSFS-039"
        link_tree.mkdir(parents=True)
        (link_tree / "NSFS-039.nfo").write_text("nfo")
        (link_tree / "poster.jpg").write_bytes(b"poster")
        lib = await repo.create_library(
            name="t", path=str(root), link_template=str(tmp_path / "linktree" / "{number}" / "{number}")
        )
        assert lib.id is not None
        meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
        assert meta.id is not None
        item = await repo.create_media_file(
            lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )

        outcome = build_footprint.sync(
            library=lib,
            items=[item],
            indexed=[item],
            metas={meta.id: meta},
            include_work_dir=False,
        )

        assert [entry.path for entry in outcome.inventory.entries] == [video]
        assert outcome.notices == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_truncation_ignores_paths_outside_root(self, repo: Repository, tmp_path: Path) -> None:
        """库外产物不进清单, 因此也不算「未纳入」: 提示里的数字只数真会进清单的路径."""
        from amane.db.models import MediaFileStatus

        root = tmp_path / "lib"
        video_dir = root / "Studio" / "NSFS-039"
        video_dir.mkdir(parents=True)
        video = video_dir / "NSFS-039.mp4"
        video.write_bytes(b"v" * 100)
        link_tree = tmp_path / "linktree" / "NSFS-039"
        link_tree.mkdir(parents=True)
        (link_tree / "NSFS-039.nfo").write_text("nfo")
        (link_tree / "poster.jpg").write_bytes(b"poster")
        lib = await repo.create_library(
            name="t", path=str(root), link_template=str(tmp_path / "linktree" / "{number}" / "{number}")
        )
        assert lib.id is not None
        meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
        assert meta.id is not None
        item = await repo.create_media_file(
            lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )

        outcome = build_footprint.sync(
            library=lib, items=[item], indexed=[item], metas={meta.id: meta}, include_work_dir=False, limit=1
        )

        assert [entry.path for entry in outcome.inventory.entries] == [video]
        assert outcome.inventory.truncated is False
        assert outcome.inventory.dropped == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_alias_index_path_is_notice(self, repo: Repository, tmp_path: Path) -> None:
        """库根与索引写法不一致 (旧库的符号链接别名) 时给出可操作的提示, 而不是静默什么都不删."""
        from amane.db.models import MediaFileStatus

        root = tmp_path / "lib"
        video_dir = root / "Studio" / "NSFS-039"
        video_dir.mkdir(parents=True)
        video = video_dir / "NSFS-039.mp4"
        video.write_bytes(b"v" * 100)
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        lib = await repo.create_library(name="t", path=str(root))
        assert lib.id is not None
        meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
        assert meta.id is not None
        item = await repo.create_media_file(
            lib.id,
            path=str(alias / "Studio" / "NSFS-039" / "NSFS-039.mp4"),
            number="NSFS-039",
            status=MediaFileStatus.SCRAPED,
            metadata_id=meta.id,
        )

        outcome = build_footprint.sync(
            library=lib, items=[item], indexed=[item], metas={meta.id: meta}, include_work_dir=False
        )

        assert outcome.inventory.entries == []
        assert any(notice.kind is FootprintNoticeKind.OUTSIDE_ROOT for notice in outcome.notices)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_work_dir_surrogate_is_not_expanded(
        self, repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """工作目录里的目录链接 (Windows 的 junction) 不展开: 库外内容不进删除集合."""
        from amane.db.models import MediaFileStatus

        root = tmp_path / "lib"
        video_dir = root / "Studio" / "NSFS-039"
        outside = tmp_path / "outside"
        (video_dir / "linked").mkdir(parents=True)
        outside.mkdir()
        (outside / "secret.nfo").write_bytes(b"secret")
        video = video_dir / "NSFS-039.mp4"
        video.write_bytes(b"v" * 100)
        lib = await repo.create_library(name="t", path=str(root))
        assert lib.id is not None
        item = await repo.create_media_file(lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED)
        monkeypatch.setattr(footprint_module, "is_name_surrogate_dir", lambda path: path.name == "linked")

        outcome = build_footprint.sync(library=lib, items=[item], indexed=[item], metas={}, include_work_dir=True)

        paths = {entry.path for entry in outcome.inventory.entries}
        assert video in paths
        assert not any(path.name == "secret.nfo" for path in paths)
