"""ARCHIVE: 黑名单与过小视频移入 `.amane_trash`; 不整理正片."""

from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.db.models import MediaFileStatus
from amane.handlers import ArchiveHandler, ArchivePayload

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository


@pytest.mark.asyncio(loop_scope="function")
async def test_archive_trashes_blacklisted_files(repo: Repository, tmp_path: Path) -> None:
    """黑名单命中文件: ARCHIVE 时移入本库 .amane_trash 并删除 MediaFile 记录.

    预告片命中 trailer_pattern: 只跳过不归档. 正片不动.
    """
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    ads = ["新片广告.mp4", "广告.jpg", "广告.torrent", "广告.url", "广告.html", "新片广告.nfo", "广告"]
    keep = ["cover.jpg", "note.txt"]
    for name in (*ads, *keep):
        (src_dir / name).write_bytes(b"x")
    video = src_dir / "NSFS-039.mp4"
    video.write_bytes(b"video")
    trailer = src_dir / "trailer.mp4"
    trailer.write_bytes(b"trailer")
    (src_dir / "广告目录").mkdir()

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["广告"])
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    ad_record = await repo.create_media_file(
        lib.id,
        path=str(src_dir / "新片广告.mp4"),
        number="NSFS-039",
        status=MediaFileStatus.SCRAPED,
        metadata_id=meta.id,
    )
    source = await repo.create_media_file(
        lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert ad_record.id is not None and source.id is not None

    handler = ArchiveHandler(repo, HotSettings())
    result = await handler.handle(ArchivePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == len(ads)
    assert result.result.failed == 0

    trash = lib_root / ".amane_trash"
    for name in ads:
        assert not (src_dir / name).exists()
        assert (trash / name).exists()
    assert await repo.get_media_file(ad_record.id) is None
    assert await repo.get_media_file(source.id) is not None
    for name in keep:
        assert (src_dir / name).exists()
    assert (src_dir / "广告目录").is_dir()
    assert trailer.exists()
    assert video.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_archive_trashes_blacklisted_outside_patterns(repo: Repository, tmp_path: Path) -> None:
    """黑名单归档不应用 library `patterns`: 即使文件名不匹配 glob, 仍移动至 `.amane_trash`."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    ad = src_dir / "新片广告.mp4"
    ad.write_bytes(b"ad")
    video = src_dir / "NSFS-039.mkv"
    video.write_bytes(b"video")

    lib = await repo.create_library(
        name="t", path=str(lib_root), write_nfo=False, patterns=["*.mkv"], blacklist_patterns=["广告"]
    )
    assert lib.id is not None

    handler = ArchiveHandler(repo, HotSettings())
    result = await handler.handle(ArchivePayload(library_id=lib.id, path=str(src_dir), patterns=["*.mkv"]))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 1
    assert not ad.exists()
    assert (lib_root / ".amane_trash" / "新片广告.mp4").exists()
    assert video.exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_archive_untracked_and_collision(repo: Repository, tmp_path: Path) -> None:
    """无 MediaFile 记录的黑名单文件同样归档; 同名冲突加 (1); 二次归档幂等."""
    lib_root = tmp_path / "lib"
    d1 = lib_root / "a"
    d2 = lib_root / "b"
    d1.mkdir(parents=True)
    d2.mkdir()
    ad1 = d1 / "AD_01.mp4"
    ad2 = d2 / "AD_01.mp4"
    ad1.write_bytes(b"ad1")
    ad2.write_bytes(b"ad2")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["(?i)ad"])
    assert lib.id is not None

    handler = ArchiveHandler(repo, HotSettings())
    result = await handler.handle(ArchivePayload(library_id=lib.id, path=str(lib_root)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 2

    assert not ad1.exists() and not ad2.exists()
    trash = lib_root / ".amane_trash"
    assert (trash / "AD_01.mp4").exists()
    assert (trash / "AD_01(1).mp4").exists()

    again = await handler.handle(ArchivePayload(library_id=lib.id, path=str(lib_root)))
    assert again.result is not None
    assert again.result.trashed == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_archive_trashes_undersized_videos_keeps_sidecars(repo: Repository, tmp_path: Path) -> None:
    """低于 min_file_size 的视频进回收站; 预告片/字幕/nfo 不因体积归档; 正片不动."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    ad = src_dir / "ad.mp4"
    ad.write_bytes(b"tiny")
    video = src_dir / "NSFS-039.mp4"
    video.write_bytes(b"x" * 200)
    trailer = src_dir / "trailer.mp4"
    trailer.write_bytes(b"t")
    (src_dir / "note.nfo").write_bytes(b"nfo")
    (src_dir / "NSFS-039.srt").write_text("sub")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, min_file_size=50)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    ad_record = await repo.create_media_file(
        lib.id, path=str(ad), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    source = await repo.create_media_file(
        lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert ad_record.id is not None and source.id is not None

    handler = ArchiveHandler(repo, HotSettings())
    result = await handler.handle(ArchivePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 1

    assert not ad.exists()
    assert (lib_root / ".amane_trash" / "ad.mp4").exists()
    assert await repo.get_media_file(ad_record.id) is None
    assert await repo.get_media_file(source.id) is not None
    assert trailer.exists()
    assert (src_dir / "note.nfo").exists()
    assert video.exists()
    assert (src_dir / "NSFS-039.srt").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_archive_path_subdirectory(repo: Repository, tmp_path: Path) -> None:
    """path 限定子目录时, 范围外的黑名单文件不动."""
    lib_root = tmp_path / "lib"
    inside = lib_root / "incoming"
    outside = lib_root / "other"
    inside.mkdir(parents=True)
    outside.mkdir()
    ad_in = inside / "广告.mp4"
    ad_out = outside / "广告.mp4"
    ad_in.write_bytes(b"in")
    ad_out.write_bytes(b"out")

    lib = await repo.create_library(name="t", path=str(lib_root), blacklist_patterns=["广告"])
    assert lib.id is not None

    handler = ArchiveHandler(repo, HotSettings())
    result = await handler.handle(ArchivePayload(library_id=lib.id, path=str(inside)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 1
    assert not ad_in.exists()
    assert ad_out.exists()
    assert (lib_root / ".amane_trash" / "广告.mp4").exists()
