"""库文件分类规则."""

from pathlib import Path

from amane.library import LibraryFileKind, LibraryScan, UnwantedKind


class TestClassify:
    """单路径判定. 回收站与无规则命中返回 None."""

    def test_media_by_extension(self):
        scan = LibraryScan()
        assert scan.classify(Path("/tmp/video.mp4")) is LibraryFileKind.MEDIA
        assert scan.classify(Path("/tmp/note.txt")) is None

    def test_trailer_is_skip(self):
        scan = LibraryScan(trailer_pattern="(?i)trailer")
        assert scan.classify(Path("/tmp/trailer.mp4")) is LibraryFileKind.SKIP
        assert scan.classify(Path("/tmp/video.mp4")) is LibraryFileKind.MEDIA

    def test_blacklist_is_trash(self):
        scan = LibraryScan(blacklist_patterns=["广告"])
        assert scan.classify(Path("/tmp/广告.html")) is LibraryFileKind.TRASH
        assert scan.classify(Path("/tmp/广告.jpg")) is LibraryFileKind.TRASH
        assert scan.classify(Path("/tmp/cover.jpg")) is None

    def test_blacklist_before_trailer(self):
        """同时命中黑名单与预告片时回收."""
        scan = LibraryScan(trailer_pattern="trailer", blacklist_patterns=["trailer"])
        assert scan.classify(Path("/tmp/trailer.mp4")) is LibraryFileKind.TRASH

    def test_undersized_video_is_trash(self, tmp_path: Path):
        small = tmp_path / "ad.mp4"
        small.write_bytes(b"tiny")
        large = tmp_path / "film.mp4"
        large.write_bytes(b"x" * 100)
        nfo = tmp_path / "note.nfo"
        nfo.write_bytes(b"nfo")
        scan = LibraryScan(min_file_size=50)
        assert scan.classify(small) is LibraryFileKind.TRASH
        assert scan.classify(large) is LibraryFileKind.MEDIA
        assert scan.classify(nfo) is None
        assert scan.classify(tmp_path / "gone.mp4") is LibraryFileKind.MEDIA

    def test_trailer_not_trashed_for_size(self, tmp_path: Path):
        trailer = tmp_path / "trailer.mp4"
        trailer.write_bytes(b"t")
        scan = LibraryScan(trailer_pattern="(?i)trailer", min_file_size=50)
        assert scan.classify(trailer) is LibraryFileKind.SKIP

    def test_patterns_select_media(self):
        scan = LibraryScan(patterns=["*.mkv"])
        assert scan.classify(Path("/tmp/a.mkv")) is LibraryFileKind.MEDIA
        assert scan.classify(Path("/tmp/a.mp4")) is None

    def test_blacklist_outside_patterns(self):
        scan = LibraryScan(patterns=["*.mkv"], blacklist_patterns=["广告"])
        assert scan.classify(Path("/tmp/广告.mp4")) is LibraryFileKind.TRASH

    def test_trash_dir_omitted(self):
        scan = LibraryScan()
        assert scan.classify(Path("/lib/.amane_trash/video.mp4")) is None


class TestUnwantedKind:
    """无效文件的命中规则; 预告片是预期文件, 不是无效文件."""

    def test_blacklist_and_size(self, tmp_path: Path):
        small = tmp_path / "small.mp4"
        small.write_bytes(b"x")
        large = tmp_path / "film.mp4"
        large.write_bytes(b"x" * 100)
        scan = LibraryScan(blacklist_patterns=["ad"], min_file_size=50)

        assert scan.unwanted_kind(tmp_path / "ad.mkv") is UnwantedKind.BLACKLIST
        assert scan.unwanted_kind(small) is UnwantedKind.UNDERSIZED
        assert scan.unwanted_kind(large) is None
        assert scan.unwanted_kind(tmp_path / "note.nfo") is None

    def test_trailer_excluded_before_size(self, tmp_path: Path):
        trailer = tmp_path / "trailer.mp4"
        trailer.write_bytes(b"x")
        scan = LibraryScan(trailer_pattern="(?i)trailer", min_file_size=50)

        assert scan.unwanted_kind(trailer) is None

    def test_trash_dir_omitted(self):
        scan = LibraryScan(blacklist_patterns=["ad"])

        assert scan.unwanted_kind(Path("/lib/.amane_trash/ad.mkv")) is None
