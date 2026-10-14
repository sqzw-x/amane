"""测试 file ops 消费已物化 URL (内部派生 / 外部) + trailer 落盘."""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from PIL import Image

from amane.config import HotSettings, WatermarkConfig
from amane.db.models import MediaFile, Metadata
from amane.enums import DownloadableResource
from amane.handlers.file import execute_file_operations
from amane.media import ResourceStore
from amane.organize import MoveMode, PlaceOutcome, ResolvedPaths
from amane.parsing import parse_file_info

if TYPE_CHECKING:
    from amane.net.http import WebClient


class FakeClient:
    """download 写出小图/视频占位."""

    async def download(self, url: str, dest: Path, **kwargs) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(".mp4"):
            dest.write_bytes(b"video")
        else:
            Image.new("RGB", (800, 538), "blue").save(dest)
        return True

    async def get_filesize(self, url: str, **kwargs):
        return None

    async def resolve_final_url(self, url: str, **kwargs) -> str:
        return url


def _pixel(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as img:
        return cast("tuple[int, int, int]", img.convert("RGB").getpixel((0, 0)))


def _paths(base: Path) -> ResolvedPaths:
    return ResolvedPaths(
        video=base / "MIDV-123" / "MIDV-123.mp4",
        thumb=base / "MIDV-123" / "thumb.jpg",
        poster=base / "MIDV-123" / "poster.jpg",
        fanart=base / "MIDV-123" / "fanart.jpg",
        extrafanart_dir=base / "MIDV-123" / "extrafanart",
        nfo=base / "MIDV-123" / "MIDV-123.nfo",
        trailer=base / "MIDV-123" / "trailer.mp4",
    )


@pytest.mark.asyncio
async def test_internal_poster_and_trailer(resource_store: ResourceStore, tmp_path: Path):
    """poster = 内部派生 URL → 从 store 取文件; trailer 下载并复制."""
    client = cast("WebClient", FakeClient())

    # 造一个裁剪派生资源, 作为 metadata 的内部 poster URL
    async def producer(dest: Path) -> bool:
        Image.new("RGB", (379, 538), "red").save(dest)
        return True

    crop = await resource_store.acquire_derived("https://s/t.jpg", "crop", "0.7", producer)
    assert crop is not None
    internal_url = f"/api/resources/{ResourceStore.url_hash(crop.url)}"

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        thumb_urls=["https://s/t.jpg"],
        poster_urls=[internal_url],
        trailer_urls=["https://s/trailer.mp4"],
    )
    out_base = tmp_path / "out"
    paths = _paths(out_base)

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.thumb.exists()  # 外部下载
    assert paths.poster.exists()  # 内部派生解析
    assert paths.trailer.exists()  # trailer 落盘
    assert paths.trailer.read_bytes() == b"video"


@pytest.mark.asyncio
async def test_target_occupied_returns_before_side_effects(resource_store: ResourceStore, tmp_path: Path):
    """目标被占用时立即返回: 不下载图片、不写 NFO、不动磁盘."""
    client = cast("WebClient", FakeClient())

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_text("existing content")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.CONFLICT
    assert result.conflict_target == paths.video
    assert paths.video.read_text() == "existing content"
    assert src.read_bytes() == b"movie"
    assert not paths.thumb.exists()
    assert not paths.nfo.exists()


@pytest.mark.asyncio
async def test_unknown_target_state_returns_before_side_effects(
    resource_store: ResourceStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """读不出目标状态时立即返回并记为失败: 不下载图片、不写 NFO、不动磁盘."""
    client = cast("WebClient", FakeClient())

    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_text("existing content")

    def denied(self: Path, other: object) -> bool:
        raise PermissionError(13, "Permission denied", str(other))

    monkeypatch.setattr(Path, "samefile", denied)

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.FAILED
    assert result.error is not None
    assert "Permission denied" in result.error
    assert result.conflict_target is None
    assert paths.video.read_text() == "existing content"
    assert src.read_bytes() == b"movie"
    assert not paths.thumb.exists()
    assert not paths.nfo.exists()


@pytest.mark.asyncio
async def test_copy_resources_skips_trailer(resource_store: ResourceStore, tmp_path: Path):
    """copy_resources 不含 trailer 时不落盘预告片, 仍复制封面."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"], trailer_urls=["https://s/trailer.mp4"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        copy_resources=[DownloadableResource.thumb, DownloadableResource.poster],
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.thumb.exists()
    assert not paths.trailer.exists()


@pytest.mark.asyncio
async def test_write_nfo_false_skips_nfo(resource_store: ResourceStore, tmp_path: Path):
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", title="T", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
        write_nfo=False,
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert not paths.nfo.exists()


@pytest.mark.asyncio
async def test_missing_internal_resource_falls_back_to_crop(resource_store: ResourceStore, tmp_path: Path):
    """poster 内部 URL 指向不存在资源 → 回退从 thumb 裁剪."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        thumb_urls=["https://s/t.jpg"],
        poster_urls=["/api/resources/deadbeefdeadbeef"],  # 不存在
    )
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src),
        web_client=client,
        config=HotSettings(),
    )
    assert result.outcome is PlaceOutcome.PLACED
    assert paths.poster.exists()  # 裁剪回退


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_watermark_respects_enabled(resource_store: ResourceStore, tmp_path: Path, enabled: bool):
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123-C.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

    mf = MediaFile(path=str(src), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src, has_external_subtitle=mf.has_external_subtitle),
        web_client=client,
        config=HotSettings(watermark=WatermarkConfig(enabled=enabled, scale=0.2)),
        watermark_dir=user_dir,
    )
    assert result.outcome is PlaceOutcome.PLACED
    with Image.open(paths.thumb) as img:
        pixel = img.convert("RGB").getpixel((20, 20))
    assert isinstance(pixel, tuple)
    if enabled:
        assert pixel[0] > 180 and pixel[2] < 80
    else:
        assert pixel[2] > 180 and pixel[0] < 80


@pytest.mark.asyncio
async def test_watermark_from_external_subtitle(resource_store: ResourceStore, tmp_path: Path):
    """文件名没有 -C 时, 库里的同目录字幕命中同样打出封面中字角标."""
    client = cast("WebClient", FakeClient())
    src = tmp_path / "src" / "MIDV-123.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"movie")

    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

    mf = MediaFile(path=str(src), library_id=1, has_external_subtitle=True)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"])
    paths = _paths(tmp_path / "out")
    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.COPY,
        resource_store=resource_store,
        file_info=parse_file_info(src, has_external_subtitle=mf.has_external_subtitle),
        web_client=client,
        config=HotSettings(watermark=WatermarkConfig(enabled=True, scale=0.2)),
        watermark_dir=user_dir,
    )
    assert result.outcome is PlaceOutcome.PLACED
    with Image.open(paths.thumb) as img:
        pixel = img.convert("RGB").getpixel((20, 20))
    assert isinstance(pixel, tuple)
    assert pixel[0] > 180 and pixel[2] < 80


@pytest.mark.asyncio
async def test_in_place_rewrites_existing_artifacts(resource_store: ResourceStore, tmp_path: Path) -> None:
    """源已在目标路径上时仍重写库路径上的旧产物: NFO 按本次元数据, 图片与预告片按本次资源.

    fanart 与 thumb 同源, 因此与 thumb 一同重写.
    """
    client = cast("WebClient", FakeClient())

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_bytes(b"movie")
    paths.nfo.write_text("旧 NFO", encoding="utf-8")
    stale = (paths.thumb, paths.poster, paths.fanart)
    before = {}
    for image in stale:
        Image.new("RGB", (800, 538), "red").save(image)
        before[image] = _pixel(image)
    paths.trailer.write_bytes(b"old trailer")

    mf = MediaFile(path=str(paths.video), library_id=1)
    meta = Metadata(
        number="MIDV-123",
        title="新标题",
        thumb_urls=["https://s/t.jpg"],
        poster_urls=["https://s/p.jpg"],
        trailer_urls=["https://s/trailer.mp4"],
    )

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(paths.video),
        web_client=client,
        config=HotSettings(),
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert result.dest == paths.video
    assert "新标题" in paths.nfo.read_text(encoding="utf-8")
    for image in stale:
        assert _pixel(image) != before[image]
    assert paths.trailer.read_bytes() == b"video"


@pytest.mark.parametrize("pre_existing", [True, False])
@pytest.mark.asyncio
async def test_fanart_follows_thumb_selection(
    resource_store: ResourceStore, tmp_path: Path, pre_existing: bool
) -> None:
    """thumb 不在 copy_resources 时 fanart 既不重写也不补建."""
    client = cast("WebClient", FakeClient())

    paths = _paths(tmp_path / "out")
    paths.video.parent.mkdir(parents=True)
    paths.video.write_bytes(b"movie")
    if pre_existing:
        Image.new("RGB", (800, 538), "red").save(paths.fanart)
    before = _pixel(paths.fanart) if pre_existing else None

    mf = MediaFile(path=str(paths.video), library_id=1)
    meta = Metadata(number="MIDV-123", thumb_urls=["https://s/t.jpg"], poster_urls=["https://s/p.jpg"])

    result = await execute_file_operations(
        media_file=mf,
        metadata=meta,
        paths=paths,
        move_mode=MoveMode.MOVE,
        resource_store=resource_store,
        file_info=parse_file_info(paths.video),
        web_client=client,
        config=HotSettings(),
        copy_resources=[DownloadableResource.poster],
    )

    assert result.outcome is PlaceOutcome.PLACED
    assert not paths.thumb.exists()
    assert paths.poster.exists()
    assert paths.fanart.exists() is pre_existing
    if before is not None:
        assert _pixel(paths.fanart) == before
