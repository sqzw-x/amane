"""tests for amane.handlers._common -- handler 间共享单元."""

from typing import TYPE_CHECKING

import pytest

from amane.db import MediaFileStatus
from amane.handlers._common import finalize_media_file, iter_media_files

if TYPE_CHECKING:
    from amane.db.repository import Repository


class TestIterMediaFiles:
    """目录遍历 + 媒体文件过滤."""

    @pytest.fixture
    def tree(self, tmp_path):
        """构造混合文件树:
        root/a.mp4, root/b.txt, root/sub/c.mkv, root/sub/d.nfo, root/sub/deep/e.avi
        """
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

    def test_recursive_extension_filter(self, tree):
        """递归 + 扩展名过滤: 仅媒体文件, 含深层."""
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None)}
        assert result == {"a.mp4", "c.mkv", "e.avi"}

    def test_non_recursive_only_top_level(self, tree):
        """非递归: 仅顶层媒体文件."""
        result = {p.name for p in iter_media_files(tree, recursive=False, patterns=None)}
        assert result == {"a.mp4"}

    def test_patterns_override_extension_filter(self, tree):
        """提供 patterns 时按 glob 匹配, 扩展名过滤失效 (可匹配 .txt/.nfo)."""
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=["*.txt", "*.nfo"])}
        assert result == {"b.txt", "d.nfo"}

    def test_patterns_no_match_yields_empty(self, tree):
        result = list(iter_media_files(tree, recursive=True, patterns=["*.iso"]))
        assert result == []

    def test_empty_dir_yields_empty(self, tmp_path):
        result = list(iter_media_files(tmp_path, recursive=True, patterns=None))
        assert result == []

    def test_directories_never_yielded(self, tree):
        """子目录本身不应被产出 (即便名字像媒体)."""
        (tree / "fake.mp4").mkdir()  # 目录但后缀像媒体
        result = list(iter_media_files(tree, recursive=True, patterns=None))
        assert all(p.is_file() for p in result)
        assert tree / "fake.mp4" not in result

    def test_skip_pattern_drops_trailer(self, tree):
        (tree / "trailer.mp4").touch()
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None, skip_patterns=["(?i)trailer"])}
        assert result == {"a.mp4", "c.mkv", "e.avi"}
        assert "trailer.mp4" not in result

    def test_skip_pattern_empty_keeps_trailer(self, tree):
        (tree / "trailer.mp4").touch()
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None, skip_patterns=[""])}
        assert "trailer.mp4" in result

    def test_skip_pattern_custom_preview_name(self, tree):
        (tree / "中文预告片.mp4").touch()
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None, skip_patterns=["预告"])}
        assert "中文预告片.mp4" not in result
        assert "a.mp4" in result

    def test_skip_patterns_any_match(self, tree):
        """多个跳过正则: 命中任一个即跳过 (预告片 + 黑名单组合)."""
        (tree / "新片广告.mp4").touch()
        (tree / "trailer.mp4").touch()
        result = {
            p.name for p in iter_media_files(tree, recursive=True, patterns=None, skip_patterns=["广告", "(?i)trailer"])
        }
        assert "新片广告.mp4" not in result
        assert "trailer.mp4" not in result
        assert "a.mp4" in result

    def test_trash_directory_never_yielded(self, tree):
        """.amane_trash (回收站) 内容不会被扫描/整理遍历."""
        trash = tree / ".amane_trash"
        trash.mkdir()
        (trash / "ad.mp4").touch()
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None)}
        assert "ad.mp4" not in result
        assert "a.mp4" in result

    def test_min_file_size_skips_small_videos_only(self, tree):
        """体积阈值只过滤扫描视频; 自定义 glob 扫到的 nfo 即使很小也保留."""
        (tree / "a.mp4").write_bytes(b"x" * 10)
        (tree / "sub" / "c.mkv").write_bytes(b"x" * 100)
        (tree / "sub" / "deep" / "e.avi").write_bytes(b"x" * 100)
        (tree / "sub" / "d.nfo").write_bytes(b"nfo")
        videos = {p.name for p in iter_media_files(tree, recursive=True, patterns=None, min_file_size=50)}
        assert videos == {"c.mkv", "e.avi"}
        mixed = {p.name for p in iter_media_files(tree, recursive=True, patterns=["*.mp4", "*.nfo"], min_file_size=50)}
        assert "a.mp4" not in mixed
        assert "d.nfo" in mixed

    def test_min_file_size_zero_disabled(self, tree):
        (tree / "a.mp4").write_bytes(b"tiny")
        result = {p.name for p in iter_media_files(tree, recursive=True, patterns=None, min_file_size=0)}
        assert "a.mp4" in result


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
