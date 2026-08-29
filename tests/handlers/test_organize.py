"""ORGANIZE: 落盘前清本库失效索引, 避免 dest 碰撞名被幽灵行占用."""

import warnings
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from amane.config import HotSettings
from amane.db.models import MediaFileStatus
from amane.enums import DownloadableResource, LinkMode
from amane.handlers import OrganizeHandler, OrganizePayload

if TYPE_CHECKING:
    from pathlib import Path

    from amane.db.repository import Repository
    from amane.media import ResourceStore


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    "stored",
    [
        [DownloadableResource.thumb],
        [DownloadableResource.thumb, DownloadableResource.poster],
        [],
        list(DownloadableResource),
    ],
)
async def test_organize_resolve_coerces_json_copy_resources(
    repo: Repository, tmp_path: Path, stored: list[DownloadableResource]
) -> None:
    """JSON 列读回是 str; resolve 必须做成 enum, 否则 model_dump 会 UnexpectedValue."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), copy_resources=stored)
    assert lib.id is not None
    payload = OrganizePayload(library_id=lib.id)
    await payload.resolve(repo)
    assert payload.copy_resources == stored
    assert all(type(item) is DownloadableResource for item in payload.copy_resources or [])
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        dumped = payload.model_dump(mode="json")
    assert dumped["copy_resources"] == [item.value for item in stored]


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_resolve_rejects_unknown_copy_resource(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root))
    assert lib.id is not None

    async def fake_get(_library_id: int):
        return SimpleNamespace(
            recursive=lib.recursive,
            patterns=lib.patterns,
            path=lib.path,
            write_nfo=lib.write_nfo,
            copy_resources=["nope"],
        )

    monkeypatch.setattr(repo, "get_library", fake_get)
    payload = OrganizePayload(library_id=lib.id)
    with pytest.raises(ValueError, match="nope"):
        await payload.resolve(repo)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_prunes_stale_collision_dest(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """模板 dest 已被另一文件占用时落到 dest(1); 幽灵占用行在落盘前被清掉, 不撞 UNIQUE."""
    lib_root = tmp_path / "lib"
    dest_dir = lib_root / "Studio" / "NSFS-039"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "NSFS-039.mp4"
    dest.write_bytes(b"first")
    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    src.write_bytes(b"second")
    stale = dest_dir / "NSFS-039(1).mp4"
    assert not stale.exists()

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None

    first = await repo.create_media_file(
        lib.id,
        path=str(dest),
        number="NSFS-039",
        status=MediaFileStatus.SCRAPED,
        metadata_id=meta.id,
    )
    occupant = await repo.create_media_file(
        lib.id,
        path=str(stale),
        number="NSFS-039",
        status=MediaFileStatus.SCRAPED,
        metadata_id=meta.id,
    )
    source = await repo.create_media_file(
        lib.id,
        path=str(src),
        number="NSFS-039",
        status=MediaFileStatus.SCRAPED,
        metadata_id=meta.id,
    )
    assert first.id is not None and occupant.id is not None and source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src.parent)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    assert dest.exists()
    assert dest.read_bytes() == b"first"
    assert stale.exists()
    assert stale.read_bytes() == b"second"
    assert not src.exists()

    assert await repo.get_media_file(occupant.id) is None
    claimed = await repo.get_media_file(source.id)
    assert claimed is not None
    assert claimed.path == str(stale)
    kept = await repo.get_media_file(first.id)
    assert kept is not None
    assert kept.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_collision_dest_free(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """碰撞 dest(1) 空闲时第二份文件落到 dest(1), 两行都保留."""
    lib_root = tmp_path / "lib"
    dest_dir = lib_root / "Studio" / "NSFS-039"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "NSFS-039.mp4"
    dest.write_bytes(b"first")
    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    src.write_bytes(b"second")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None

    first = await repo.create_media_file(
        lib.id, path=str(dest), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert first.id is not None and source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src.parent)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest1 = dest_dir / "NSFS-039(1).mp4"
    assert dest.exists()
    assert dest1.exists()
    updated = await repo.get_media_file(source.id)
    assert updated is not None
    assert updated.path == str(dest1)
    kept = await repo.get_media_file(first.id)
    assert kept is not None
    assert kept.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_appends_cd_suffix(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """源文件名含分集标记 (CD1) 时, 目标文件名按库的 cd_suffix_template 追加后缀."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039-CD1.mp4"
    src.write_bytes(b"cd1")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest = lib_root / "Studio" / "NSFS-039" / "NSFS-039-CD1.mp4"
    assert dest.exists()
    assert not src.exists()
    updated = await repo.get_media_file(source.id)
    assert updated is not None
    assert updated.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_dash_number_suffix(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """裸数字分集 (-2) 识别: NSFS-039-2.mp4 按默认后缀整理为 NSFS-039-CD2.mp4."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039-2.mp4"
    src.write_bytes(b"part2")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest = lib_root / "Studio" / "NSFS-039" / "NSFS-039-CD2.mp4"
    assert dest.exists()
    assert not src.exists()
    updated = await repo.get_media_file(source.id)
    assert updated is not None
    assert updated.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_cd_pair_no_collision(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """CD1/CD2 一对文件同批整理: 各自带后缀落盘, 不再碰撞改名为 (1)."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src1 = src_dir / "NSFS-039-CD1.mp4"
    src2 = src_dir / "NSFS-039-CD2.mp4"
    src1.write_bytes(b"cd1")
    src2.write_bytes(b"cd2")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    first = await repo.create_media_file(
        lib.id, path=str(src1), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    second = await repo.create_media_file(
        lib.id, path=str(src2), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert first.id is not None and second.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0
    assert result.result.organized == 2

    dest_dir = lib_root / "Studio" / "NSFS-039"
    dest1 = dest_dir / "NSFS-039-CD1.mp4"
    dest2 = dest_dir / "NSFS-039-CD2.mp4"
    assert dest1.exists()
    assert dest2.exists()
    assert not (dest_dir / "NSFS-039(1).mp4").exists()
    claimed1 = await repo.get_media_file(first.id)
    claimed2 = await repo.get_media_file(second.id)
    assert claimed1 is not None and claimed2 is not None
    assert claimed1.path == str(dest1)
    assert claimed2.path == str(dest2)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_custom_cd_suffix(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """自定义 cd_suffix_template 生效 (模板格式无需可反推, 配置者自行保证幂等)."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039-CD2.mp4"
    src.write_bytes(b"cd2")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, cd_suffix_template="-第{cd}集")
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest = lib_root / "Studio" / "NSFS-039" / "NSFS-039-第2集.mp4"
    assert dest.exists()
    updated = await repo.get_media_file(source.id)
    assert updated is not None
    assert updated.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_trashes_blacklisted_files(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """黑名单命中文件: ORGANIZE 时移入库根 .amane_trash 并删除 MediaFile 记录, 正片正常落盘.

    预告片命中 trailer_pattern: 只跳过不归档.
    """
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    ad = src_dir / "新片广告.mp4"
    ad.write_bytes(b"ad")
    video = src_dir / "NSFS-039.mp4"
    video.write_bytes(b"video")
    trailer = src_dir / "trailer.mp4"
    trailer.write_bytes(b"trailer")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["广告"])
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

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 1
    assert result.result.organized == 1
    assert result.result.failed == 0

    assert not ad.exists()
    assert (lib_root / ".amane_trash" / "新片广告.mp4").exists()
    assert await repo.get_media_file(ad_record.id) is None
    # 预告片保留原地 (跳过但不动), 正片落盘
    assert trailer.exists()
    assert not video.exists()
    assert (lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_trash_untracked_and_collision(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """无 MediaFile 记录的黑名单文件同样归档; 同名冲突加 (1); 二次整理幂等."""
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

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 2
    assert result.result.organized == 0

    assert not ad1.exists() and not ad2.exists()
    trash = lib_root / ".amane_trash"
    assert (trash / "AD_01.mp4").exists()
    assert (trash / "AD_01(1).mp4").exists()

    # 二次整理: 回收站内容不被再次归档
    again = await org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root)))
    assert again.result is not None
    assert again.result.trashed == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_trashes_undersized_videos_keeps_sidecars(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """低于 min_file_size 的视频进回收站; 预告片/字幕/nfo 不因体积归档; 正片落盘."""
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

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.trashed == 1
    assert result.result.organized == 1

    assert not ad.exists()
    assert (lib_root / ".amane_trash" / "ad.mp4").exists()
    assert await repo.get_media_file(ad_record.id) is None
    assert trailer.exists()
    assert (src_dir / "note.nfo").exists()
    dest_dir = lib_root / "Studio" / "NSFS-039"
    assert (dest_dir / "NSFS-039.mp4").exists()
    assert (dest_dir / "NSFS-039.srt").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_moves_same_dir_subtitles(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """同目录多个字幕全部搬走, 保持原文件名."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039.mp4"
    src.write_bytes(b"video")
    (src_dir / "chs.srt").write_text("sub1")
    (src_dir / "NSFS-039.zh.ass").write_text("sub2")
    (src_dir / "readme.txt").write_text("skip")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest_dir = lib_root / "Studio" / "NSFS-039"
    assert (dest_dir / "NSFS-039.mp4").exists()
    assert (dest_dir / "chs.srt").read_text() == "sub1"
    assert (dest_dir / "NSFS-039.zh.ass").read_text() == "sub2"
    assert (src_dir / "readme.txt").exists()
    assert not (src_dir / "chs.srt").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_subtitles_follow_cd(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """多分集: 有 CD 的字幕跟对应集, 解析不出的跟第一集."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src1 = src_dir / "NSFS-039-CD1.mp4"
    src2 = src_dir / "NSFS-039-CD2.mp4"
    src1.write_bytes(b"cd1")
    src2.write_bytes(b"cd2")
    (src_dir / "a-CD1.srt").write_text("cd1sub")
    (src_dir / "b-CD2.ass").write_text("cd2sub")
    (src_dir / "chs.srt").write_text("unparsed")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    for src in (src1, src2):
        mf = await repo.create_media_file(
            lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )
        assert mf.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest_dir = lib_root / "Studio" / "NSFS-039"
    assert (dest_dir / "a-CD1.srt").read_text() == "cd1sub"
    assert (dest_dir / "chs.srt").read_text() == "unparsed"
    assert (dest_dir / "b-CD2.ass").read_text() == "cd2sub"


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_empty_subtitle_extensions_leaves_subs(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """空扩展名列表关闭字幕发现."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039.mp4"
    src.write_bytes(b"video")
    sub = src_dir / "chs.srt"
    sub.write_text("keep")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, subtitle_extensions=[])
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert sub.exists()
    assert not (lib_root / "Studio" / "NSFS-039" / "chs.srt").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_writes_strm_and_nfo_next_to_link(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """视频在库内整理; strm + 默认 NFO 写到库外 link_template."""
    lib_root = tmp_path / "lib"
    local = tmp_path / "emby"
    lib_root.mkdir()
    local.mkdir()
    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    src.write_bytes(b"video")

    lib = await repo.create_library(
        name="t",
        path=str(lib_root),
        link_template=str(local / "{studio}" / "{number}" / "{number}.{ext}"),
        link_mode=LinkMode.STRM,
        copy_resources=[],
    )
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    media = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert media.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store, safe_dirs=[tmp_path])
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src.parent)))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 1
    assert result.result.failed == 0

    dest = lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4"
    strm = local / "Studio" / "NSFS-039" / "NSFS-039.strm"
    nfo = local / "Studio" / "NSFS-039" / "NSFS-039.nfo"
    assert dest.exists()
    assert not src.exists()
    assert strm.read_text(encoding="utf-8") == f"{dest}\n"
    assert nfo.exists()
    updated = await repo.get_media_file(media.id)
    assert updated is not None
    assert updated.path == str(dest)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_strm_content_template_writes_remote_path(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """strm 内容按模板渲染成远端路径 (rclone 挂载点 → OpenList), 而不是本地绝对路径."""
    lib_root = tmp_path / "lib"
    local = tmp_path / "emby"
    lib_root.mkdir()
    local.mkdir()
    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    src.write_bytes(b"video")

    lib = await repo.create_library(
        name="t",
        path=str(lib_root),
        video_template="OD/VC/{number}/{number}.{ext}",
        link_template=str(local / "{number}" / "{number}.{ext}"),
        link_mode=LinkMode.STRM,
        strm_content_template="/{video_relpath}",
        copy_resources=[],
    )
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    media = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert media.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store, safe_dirs=[tmp_path])
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src.parent)))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 1
    assert result.result.failed == 0

    dest = lib_root / "OD" / "VC" / "NSFS-039" / "NSFS-039.mp4"
    strm = local / "NSFS-039" / "NSFS-039.strm"
    assert dest.exists()
    # 库根前缀被剥掉, 只留库内相对段 -- MediaWarp AlistStrm 需要的形态
    assert strm.read_text(encoding="utf-8") == "/OD/VC/NSFS-039/NSFS-039.mp4\n"


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_strm_relpath_outside_library_root_fails(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """视频经绝对模板落到库根之外时 {video_relpath} 无解: 记失败不写 strm, 但 dest 仍回写."""
    lib_root = tmp_path / "lib"
    disk2 = tmp_path / "disk2"
    local = tmp_path / "emby"
    for d in (lib_root, disk2, local):
        d.mkdir()
    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    src.write_bytes(b"video")

    lib = await repo.create_library(
        name="t",
        path=str(lib_root),
        video_template=str(disk2 / "{number}" / "{number}.{ext}"),
        link_template=str(local / "{number}" / "{number}.{ext}"),
        link_mode=LinkMode.STRM,
        strm_content_template="/{video_relpath}",
        copy_resources=[],
    )
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    media = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert media.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store, safe_dirs=[tmp_path])
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src.parent)))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 0
    assert result.result.failed == 1

    dest = disk2 / "NSFS-039" / "NSFS-039.mp4"
    assert dest.exists()
    assert not (local / "NSFS-039" / "NSFS-039.strm").exists()
    # 视频已搬家, path 仍要回写, 否则索引指向空位
    updated = await repo.get_media_file(media.id)
    assert updated is not None
    assert updated.path == str(dest)
