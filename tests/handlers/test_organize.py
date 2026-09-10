"""ORGANIZE: 只处理范围内的索引, 落盘前删除本次读到的失效行."""

import asyncio
import warnings
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from amane.config import HotSettings
from amane.db.models import MediaFileStatus
from amane.enums import DownloadableResource, LinkMode, MoveMode
from amane.handlers import OrganizeHandler, OrganizePayload
from amane.handlers.file import commit_organized_media_file

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
    assert dumped["copy_resources"] == list(stored)


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
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root)))
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
    """源文件名含分集标记 (CD1) 时, 默认模板可选组写出 -CD1."""
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
    """自定义分集可选组生效 (模板格式无需可反推, 配置者自行保证幂等)."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    src = src_dir / "NSFS-039-CD2.mp4"
    src.write_bytes(b"cd2")

    lib = await repo.create_library(
        name="t",
        path=str(lib_root),
        write_nfo=False,
        video_template="{studio}/{number}/{number}[-第{cd?}集].{ext}",
    )
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
async def test_organize_writes_subtitle_tag(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """源文件名含 -C 时, 默认模板可选组写出 -C; 无标记则不写."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    tagged = src_dir / "NSFS-039-C.mp4"
    tagged.write_bytes(b"sub")
    bare = src_dir / "NSFS-040.mp4"
    bare.write_bytes(b"bare")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta_c = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    meta_b = await repo.upsert_metadata(number="NSFS-040", studio="Studio")
    assert meta_c.id is not None and meta_b.id is not None
    src_c = await repo.create_media_file(
        lib.id, path=str(tagged), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta_c.id
    )
    src_b = await repo.create_media_file(
        lib.id, path=str(bare), number="NSFS-040", status=MediaFileStatus.SCRAPED, metadata_id=meta_b.id
    )
    assert src_c.id is not None and src_b.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest_c = lib_root / "Studio" / "NSFS-039" / "NSFS-039-C.mp4"
    dest_b = lib_root / "Studio" / "NSFS-040" / "NSFS-040.mp4"
    assert dest_c.exists()
    assert dest_b.exists()
    updated_c = await repo.get_media_file(src_c.id)
    updated_b = await repo.get_media_file(src_b.id)
    assert updated_c is not None and updated_c.path == str(dest_c)
    assert updated_b is not None and updated_b.path == str(dest_b)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_does_not_trash_blacklisted(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """整理不扫描磁盘、不回收; 黑名单文件即使在范围内也留在原路径."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    ad = src_dir / "新片广告.mp4"
    ad.write_bytes(b"ad")
    video = src_dir / "NSFS-039.mp4"
    video.write_bytes(b"video")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False, blacklist_patterns=["广告"])
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 1
    assert ad.exists()
    assert not (lib_root / ".amane_trash").exists()
    assert (lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_media_file_ids_only(repo: Repository, resource_store: ResourceStore, tmp_path: Path) -> None:
    """勾选快照只整理给出的 id; 其它已刮削文件不动."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    keep = src_dir / "KEEP-001.mp4"
    target = src_dir / "NSFS-039.mp4"
    keep.write_bytes(b"keep")
    target.write_bytes(b"video")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta_keep = await repo.upsert_metadata(number="KEEP-001", studio="Studio")
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta_keep.id is not None and meta.id is not None
    kept = await repo.create_media_file(
        lib.id, path=str(keep), number="KEEP-001", status=MediaFileStatus.SCRAPED, metadata_id=meta_keep.id
    )
    source = await repo.create_media_file(
        lib.id, path=str(target), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert kept.id is not None and source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, media_file_ids=[source.id]))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 1
    assert keep.exists()
    assert not target.exists()
    assert (lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4").exists()
    remaining = await repo.get_media_file(kept.id)
    assert remaining is not None and remaining.path == str(keep)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_path_prefix_does_not_prune_outside(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """局部整理只探活本次读到的行; 范围外的失效索引保留."""
    lib_root = tmp_path / "lib"
    inside = lib_root / "incoming"
    inside.mkdir(parents=True)
    video = inside / "NSFS-039.mp4"
    video.write_bytes(b"video")
    ghost = lib_root / "other" / "gone.mp4"

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    stale = await repo.create_media_file(lib.id, path=str(ghost), number="GONE-001")
    assert source.id is not None and stale.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(inside)))
    assert result.success is True
    assert await repo.get_media_file(stale.id) is not None
    assert await repo.get_media_file(source.id) is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_resolve_rejects_ids_and_path(repo: Repository, tmp_path: Path) -> None:
    """显式 path 与 media_file_ids 不能同时给出."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root))
    assert lib.id is not None
    payload = OrganizePayload(library_id=lib.id, path=str(lib_root), media_file_ids=[1])
    with pytest.raises(HTTPException) as ei:
        await payload.resolve(repo)
    assert ei.value.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_serializes_same_library(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """同库两个 ORGANIZE 执行期串行, 两次都能完成."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    video = src_dir / "NSFS-039.mp4"
    video.write_bytes(b"video")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(video), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert source.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    first, second = await asyncio.gather(
        org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root))),
        org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root))),
    )
    assert first.success is True and second.success is True
    assert (lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4").exists()


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
async def test_organize_pairs_flat_subtitles_by_number(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """平铺目录: 字幕按番号配对, 不会全部进入第一部."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    first = src_dir / "ABC-123.mp4"
    second = src_dir / "DEF-456.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    (src_dir / "ABC-123.srt").write_text("first")
    (src_dir / "DEF-456.srt").write_text("second")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta_a = await repo.upsert_metadata(number="ABC-123", studio="Studio")
    meta_b = await repo.upsert_metadata(number="DEF-456", studio="Studio")
    assert meta_a.id is not None and meta_b.id is not None
    for src, number, meta_id in (
        (first, "ABC-123", meta_a.id),
        (second, "DEF-456", meta_b.id),
    ):
        mf = await repo.create_media_file(
            lib.id, path=str(src), number=number, status=MediaFileStatus.SCRAPED, metadata_id=meta_id
        )
        assert mf.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest_a = lib_root / "Studio" / "ABC-123"
    dest_b = lib_root / "Studio" / "DEF-456"
    assert (dest_a / "ABC-123.srt").read_text() == "first"
    assert (dest_b / "DEF-456.srt").read_text() == "second"
    assert not (dest_a / "DEF-456.srt").exists()
    assert not (dest_b / "ABC-123.srt").exists()


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_pairs_number_and_cd_with_multi_lang(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """两番号 × 两分集: 各集只带走本番号本分集的多语字幕."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    videos = {
        "ABC-123-CD1.mp4": "a1",
        "ABC-123-CD2.mp4": "a2",
        "DEF-456-CD1.mp4": "d1",
        "DEF-456-CD2.mp4": "d2",
    }
    for name, body in videos.items():
        (src_dir / name).write_bytes(body.encode())
    (src_dir / "ABC-123-CD1.chs.srt").write_text("a1-chs")
    (src_dir / "ABC-123-CD1.en.srt").write_text("a1-en")
    (src_dir / "ABC-123-CD2.chs.srt").write_text("a2-chs")
    (src_dir / "ABC-123-CD2.en.srt").write_text("a2-en")
    (src_dir / "DEF-456-CD1.srt").write_text("d1-sub")
    (src_dir / "DEF-456-CD2.ass").write_text("d2-sub")

    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta_a = await repo.upsert_metadata(number="ABC-123", studio="Studio")
    meta_b = await repo.upsert_metadata(number="DEF-456", studio="Studio")
    assert meta_a.id is not None and meta_b.id is not None
    for name, number, meta_id in (
        ("ABC-123-CD1.mp4", "ABC-123", meta_a.id),
        ("ABC-123-CD2.mp4", "ABC-123", meta_a.id),
        ("DEF-456-CD1.mp4", "DEF-456", meta_b.id),
        ("DEF-456-CD2.mp4", "DEF-456", meta_b.id),
    ):
        mf = await repo.create_media_file(
            lib.id, path=str(src_dir / name), number=number, status=MediaFileStatus.SCRAPED, metadata_id=meta_id
        )
        assert mf.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0

    dest_a = lib_root / "Studio" / "ABC-123"
    dest_b = lib_root / "Studio" / "DEF-456"
    assert (dest_a / "ABC-123-CD1.chs.srt").read_text() == "a1-chs"
    assert (dest_a / "ABC-123-CD1.en.srt").read_text() == "a1-en"
    assert (dest_a / "ABC-123-CD2.chs.srt").read_text() == "a2-chs"
    assert (dest_a / "ABC-123-CD2.en.srt").read_text() == "a2-en"
    assert (dest_b / "DEF-456-CD1.srt").read_text() == "d1-sub"
    assert (dest_b / "DEF-456-CD2.ass").read_text() == "d2-sub"
    assert not (dest_a / "DEF-456-CD1.srt").exists()
    assert not (dest_b / "ABC-123-CD1.chs.srt").exists()


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
async def test_organize_strm_content_template_uses_actual_dest(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """正文用实际整理后的路径相对库根目录; 引用 relpath 且目标路径在库外时失败, 目标路径仍回写."""
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
        link_template=str(local / "{number}.{ext}"),
        link_mode=LinkMode.STRM,
        strm_content_template="/{video_relpath}",
        copy_resources=[],
        write_nfo=False,
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
    dest = lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4"
    strm = local / "NSFS-039.strm"
    assert dest.exists()
    assert strm.read_text(encoding="utf-8") == "/Studio/NSFS-039/NSFS-039.mp4\n"


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("kind", ["two_files", "empty", "missing_subdir", "missing_library"])
async def test_reports_progress(repo: Repository, resource_store: ResourceStore, tmp_path: Path, kind: str) -> None:
    """范围内索引按条数上报 determinate 进度; 跳过的文件仍计入 current. 库不存在不上报."""
    lib_root = tmp_path / "lib"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None

    events: list[tuple[int, int, str]] = []

    async def capture(current: int, total: int, message: str = "") -> None:
        events.append((current, total, message))

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    org.set_progress_callback(capture)

    if kind == "missing_subdir":
        result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir / "missing")))
        assert result.success is True
        assert result.result is not None
        assert result.result.organized == 0
        assert events[-1] == (1, 1, "done")
        return

    if kind == "missing_library":
        result = await org.handle(OrganizePayload(library_id=lib.id + 999, path=str(src_dir)))
        assert result.success is False
        assert result.error is not None
        assert "not found" in result.error
        assert events == []
        return

    if kind == "empty":
        result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
        assert result.success is True
        assert result.result is not None
        assert result.result.organized == 0
        assert events[-1] == (1, 1, "done")
        return

    src1 = src_dir / "NSFS-039.mp4"
    src2 = src_dir / "SKIP-001.mp4"
    src1.write_bytes(b"a")
    src2.write_bytes(b"b")
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    await repo.create_media_file(
        lib.id, path=str(src1), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    await repo.create_media_file(lib.id, path=str(src2), number="SKIP-001")

    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.organized == 1
    assert result.result.skipped == 1

    assert (0, 2, "prune") in events
    assert (0, 2, "organize") in events
    organize_at = events.index((0, 2, "organize"))
    after = events[organize_at:]
    assert after[-1] == (2, 2, "done")
    assert any(m == src1.name for _, _, m in after)
    assert any(m == src2.name for _, _, m in after)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    "case",
    [
        "inside_update",
        "inside_occupant",
        "outside_source_gone",
        "outside_source_kept",
    ],
)
async def test_commit_organized_media_file(repo: Repository, tmp_path: Path, case: str) -> None:
    """目标路径在本库内则改 path; 已被占用则删本行; 不在本库内且源不在磁盘则删行."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None

    src = lib_root / "incoming" / "NSFS-039.mp4"
    src.parent.mkdir()
    placed = lib_root / "Studio" / "NSFS-039" / "NSFS-039.mp4"
    outside = tmp_path / "other" / "NSFS-039.mp4"

    if case == "inside_update":
        src.write_bytes(b"v")
        media = await repo.create_media_file(
            lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )
        assert media.id is not None
        await commit_organized_media_file(repo, media, placed, lib_root)
        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.path == str(placed)
        return

    if case == "inside_occupant":
        src.write_bytes(b"v")
        media = await repo.create_media_file(
            lib.id,
            path=str(src),
            number="NSFS-039",
            status=MediaFileStatus.SCRAPED,
            metadata_id=meta.id,
            oshash="abc",
        )
        occupant = await repo.create_media_file(lib.id, path=str(placed), number=None)
        assert media.id is not None and occupant.id is not None
        await commit_organized_media_file(repo, media, placed, lib_root)
        assert await repo.get_media_file(media.id) is None
        kept = await repo.get_media_file(occupant.id)
        assert kept is not None
        assert kept.metadata_id == meta.id
        assert kept.status == MediaFileStatus.SCRAPED
        assert kept.oshash == "abc"
        assert kept.number == "NSFS-039"
        return

    if case == "outside_source_gone":
        media = await repo.create_media_file(
            lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
        )
        assert media.id is not None
        outside.parent.mkdir()
        outside.write_bytes(b"v")
        await commit_organized_media_file(repo, media, outside, lib_root)
        assert await repo.get_media_file(media.id) is None
        assert await repo.get_metadata(meta.id) is not None
        return

    src.write_bytes(b"v")
    media = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    assert media.id is not None
    outside.parent.mkdir()
    outside.write_bytes(b"copy")
    await commit_organized_media_file(repo, media, outside, lib_root)
    kept = await repo.get_media_file(media.id)
    assert kept is not None
    assert kept.path == str(src)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("mode", [MoveMode.MOVE, MoveMode.COPY])
async def test_organize_placed_outside_library(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path, mode: MoveMode
) -> None:
    """目标路径不在本库内: move 删行; copy 保留源路径. 目标路径上已有占用行时不触发 UNIQUE."""
    lib_root = tmp_path / "download"
    other = tmp_path / "media"
    src_dir = lib_root / "incoming"
    src_dir.mkdir(parents=True)
    other.mkdir()
    src = src_dir / "NSFS-039.mp4"
    src.write_bytes(b"video")
    placed = other / "Studio" / "NSFS-039" / "NSFS-039.mp4"

    lib = await repo.create_library(
        name="dl",
        path=str(lib_root),
        write_nfo=False,
        copy_resources=[],
        move_mode=mode,
        video_template=f"{other.as_posix()}/{{studio}}/{{number}}/{{number}}.{{ext}}",
    )
    assert lib.id is not None
    store_lib = await repo.create_library(name="store", path=str(other), write_nfo=False)
    assert store_lib.id is not None
    meta = await repo.upsert_metadata(number="NSFS-039", studio="Studio")
    assert meta.id is not None
    source = await repo.create_media_file(
        lib.id, path=str(src), number="NSFS-039", status=MediaFileStatus.SCRAPED, metadata_id=meta.id
    )
    occupant = await repo.create_media_file(store_lib.id, path=str(placed), number="NSFS-039")
    assert source.id is not None and occupant.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store, safe_dirs=[tmp_path])
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(src_dir)))
    assert result.success is True
    assert result.result is not None
    assert result.result.failed == 0
    assert result.result.organized == 1
    assert placed.exists()
    assert await repo.get_metadata(meta.id) is not None
    assert await repo.get_media_file(occupant.id) is not None
    remaining = await repo.get_media_file(source.id)
    if mode is MoveMode.MOVE:
        assert not src.exists()
        assert remaining is None
    else:
        assert src.exists()
        assert remaining is not None
        assert remaining.path == str(src)


@pytest.mark.asyncio(loop_scope="function")
async def test_organize_prunes_path_outside_library_root(
    repo: Repository, resource_store: ResourceStore, tmp_path: Path
) -> None:
    """落盘前删除不在本库内的索引, 即使该文件仍在磁盘上."""
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    stray = tmp_path / "away" / "NSFS-039.mp4"
    stray.parent.mkdir()
    stray.write_bytes(b"out")
    lib = await repo.create_library(name="t", path=str(lib_root), write_nfo=False)
    assert lib.id is not None
    stray_row = await repo.create_media_file(lib.id, path=str(stray), number="NSFS-039")
    assert stray_row.id is not None

    org = OrganizeHandler(repo, HotSettings(), resource_store)
    result = await org.handle(OrganizePayload(library_id=lib.id, path=str(lib_root)))
    assert result.success is True
    assert await repo.get_media_file(stray_row.id) is None
    assert stray.exists()
