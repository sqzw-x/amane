"""tests for amane.handlers._common -- handler 间共享单元."""

from typing import TYPE_CHECKING

import pytest

from amane.db import MediaFileStatus
from amane.handlers._common import ensure_oshash, finalize_media_file, scan_library
from amane.library import LibraryFileKind, LibraryHit, LibraryScan

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


def _names(hits: list[LibraryHit], kind: LibraryFileKind) -> set[str]:
    return {h.path.name for h in hits if h.kind is kind}


class TestScanLibrary:
    """扫库遍历 + 分类."""

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.txt").touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.mkv").touch()
        (sub / "d.nfo").touch()
        deep = sub / "deep"
        deep.mkdir()
        (deep / "e.avi").touch()
        return tmp_path

    def test_recursive_media(self, tree: Path):
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan())
        assert _names(hits, LibraryFileKind.MEDIA) == {"a.mp4", "c.mkv", "e.avi"}
        assert _names(hits, LibraryFileKind.SKIP) == set()
        assert _names(hits, LibraryFileKind.TRASH) == set()

    def test_non_recursive_top_level(self, tree: Path):
        hits = scan_library.sync(tree, recursive=False, scan=LibraryScan())
        assert _names(hits, LibraryFileKind.MEDIA) == {"a.mp4"}

    def test_patterns_and_blacklist(self, tree: Path):
        (tree / "广告.html").touch()
        (tree / "trailer.mp4").touch()
        hits = scan_library.sync(
            tree,
            recursive=True,
            scan=LibraryScan(patterns=["*.txt", "*.nfo"], trailer_pattern="(?i)trailer", blacklist_patterns=["广告"]),
        )
        assert _names(hits, LibraryFileKind.MEDIA) == {"b.txt", "d.nfo"}
        assert _names(hits, LibraryFileKind.SKIP) == {"trailer.mp4"}
        assert _names(hits, LibraryFileKind.TRASH) == {"广告.html"}

    def test_empty_dir(self, tmp_path: Path):
        assert scan_library.sync(tmp_path, recursive=True, scan=LibraryScan()) == []

    def test_directories_and_trash_omitted(self, tree: Path):
        (tree / "fake.mp4").mkdir()
        trash = tree / ".amane_trash"
        trash.mkdir()
        (trash / "ad.mp4").touch()
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan())
        assert all(h.path.is_file() for h in hits)
        assert {h.path.name for h in hits} == {"a.mp4", "c.mkv", "e.avi"}

    def test_min_file_size(self, tree: Path):
        (tree / "a.mp4").write_bytes(b"x" * 10)
        (tree / "sub" / "c.mkv").write_bytes(b"x" * 100)
        (tree / "sub" / "deep" / "e.avi").write_bytes(b"x" * 100)
        hits = scan_library.sync(tree, recursive=True, scan=LibraryScan(min_file_size=50))
        assert _names(hits, LibraryFileKind.MEDIA) == {"c.mkv", "e.avi"}
        assert _names(hits, LibraryFileKind.TRASH) == {"a.mp4"}

    @pytest.mark.asyncio
    async def test_in_thread_matches_sync(self, tree: Path):
        scan = LibraryScan()
        assert await scan_library(tree, recursive=True, scan=scan) == scan_library.sync(tree, recursive=True, scan=scan)


class TestEnsureOshash:
    """按需计算指纹; 已有值不重算, 失败留 None."""

    @pytest.mark.asyncio
    async def test_computes_and_persists(self, repo: Repository, tmp_path):
        video = tmp_path / "MIDV-123.mkv"
        video.write_bytes(bytes(range(256)) * (65536 * 2 // 256))
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None
        assert await ensure_oshash(repo, media) == "a0601fdf9f610000"
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash == "a0601fdf9f610000"

    @pytest.mark.asyncio
    async def test_reuses_existing(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/nope.mp4")
        assert media.id is not None
        await repo.update_media_file(media.id, oshash="already")
        media = await repo.get_media_file(media.id)
        assert media is not None
        assert await ensure_oshash(repo, media) == "already"

    @pytest.mark.asyncio
    async def test_tiny_file_stays_none(self, repo: Repository, tmp_path):
        video = tmp_path / "MIDV-123.mp4"
        video.write_bytes(b"tiny")
        media = await repo.create_media_file(library_id=1, path=str(video))
        assert media.id is not None
        assert await ensure_oshash(repo, media) is None
        stored = await repo.get_media_file(media.id)
        assert stored is not None
        assert stored.oshash is None


class TestFinalizeMediaFile:
    """标记 MediaFile 已刮削并关联 Metadata."""

    @pytest.mark.asyncio
    async def test_updates_status_and_metadata(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/m/ABC-123.mp4")
        assert media.id is not None
        await finalize_media_file(repo, media.id, metadata_id=42)

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED
        assert updated.metadata_id == 42

    @pytest.mark.asyncio
    async def test_none_media_file_id_is_noop(self, repo: Repository):
        # 不应抛异常
        await finalize_media_file(repo, None, metadata_id=42)

    @pytest.mark.asyncio
    async def test_none_metadata_id_still_marks_scraped(self, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/m/ABC-456.mp4")
        assert media.id is not None
        await finalize_media_file(repo, media.id, metadata_id=None)

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.status == MediaFileStatus.SCRAPED
