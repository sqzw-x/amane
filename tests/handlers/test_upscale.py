"""测试 UPSCALE handler - 扫描资源就地超分, 跳过判据为标记加盘上字节."""

import io
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from amane.config import HotSettings
from amane.handlers import UpscaleHandler, UpscalePayload
from amane.handlers import upscale as upscale_mod
from amane.media import ResourceStore, UpscaleOutcome, derived_locator

if TYPE_CHECKING:
    from pathlib import Path

_URL = "https://s/small.jpg"
_LOCATOR = derived_locator(_URL, "crop", "0.7")  # 派生资源的 locator 才是 Resource.url
_LOW_QUALITY_SIZE = (800, 538)  # 最长边 800 < 阈值 1200
_SR_SIZE = (1600, 1076)


def _jpeg_bytes(size: tuple[int, int], color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


async def _seed_image(store: ResourceStore, url: str, size: tuple[int, int]) -> None:
    # 字节一律经 _jpeg_bytes 得到: PIL 写文件与写缓冲区的结果不逐字节相同.
    data = _jpeg_bytes(size, "blue")

    async def producer(dest: Path) -> bool:
        dest.write_bytes(data)
        return True

    await store.acquire_derived(url, "crop", "0.7", producer)


async def _seed_upscaled(store: ResourceStore, url: str, *, stale_on_disk: bool) -> bytes:
    """造一行已有 sr 标记的超分结果; ``stale_on_disk`` 时把盘上换回低质原图. 返回盘上字节."""
    await _seed_image(store, url, _LOW_QUALITY_SIZE)
    record = await store.get_by_url(derived_locator(url, "crop", "0.7"))
    assert record is not None
    full = store.full_path(record)
    original = _jpeg_bytes(_LOW_QUALITY_SIZE, "blue")
    assert full.read_bytes() == original

    sr_bytes = _jpeg_bytes(_SR_SIZE, "green")

    async def sr_producer(src: Path, out: Path) -> bool:
        out.write_bytes(sr_bytes)
        return True

    assert await store.upscale_in_place(record, {"scale": 2}, sr_producer) is UpscaleOutcome.UPSCALED
    baseline = full.read_bytes()
    if stale_on_disk:
        # 并发刮削把原图写回: 记录不变, 盘上的字节变了, 大小也回到低质原图的字节数.
        full.write_bytes(original)
        assert full.stat().st_size == len(original)
    return baseline


@pytest.fixture
def _fake_sr(monkeypatch):
    from amane.sr.run import SrResult

    async def fake_run_sr(inp, out, cfg, data_dir, **kwargs):
        out.write_bytes(_jpeg_bytes(_SR_SIZE, "green"))
        return SrResult(success=True, output=out)

    monkeypatch.setattr(upscale_mod, "run_SR", fake_run_sr)


@pytest.mark.asyncio
async def test_upscales_low_quality(resource_store: ResourceStore, _fake_sr):
    await _seed_image(resource_store, _URL, _LOW_QUALITY_SIZE)
    cfg = HotSettings()
    handler = UpscaleHandler(resource_store, cfg)
    result = await handler.handle(UpscalePayload())
    assert result.success
    assert result.result is not None
    assert result.result.upscaled == 1
    # 已打 sr 标记
    all_res = await resource_store.list_all()
    assert len(all_res) == 1
    res = await resource_store.get_by_url(all_res[0].url)
    assert res is not None and res.meta is not None and "sr" in res.meta


@pytest.mark.asyncio
async def test_skips_already_upscaled(resource_store: ResourceStore, _fake_sr):
    await _seed_image(resource_store, _URL, _LOW_QUALITY_SIZE)
    handler = UpscaleHandler(resource_store, HotSettings())
    await handler.handle(UpscalePayload())  # 第一次超分
    result = await handler.handle(UpscalePayload())  # 第二次应全部跳过
    assert result.result is not None
    assert result.result.upscaled == 0
    assert result.result.skipped == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale_on_disk", "expect_upscaled", "expect_skipped"),
    [
        (True, 1, 0),  # 记录为超分结果而盘上是原图 → 重跑并重记
        (False, 0, 1),  # 标记所指的字节仍在盘上 → 跳过, 不重复超分
    ],
)
async def test_reruns_when_marker_bytes_gone(
    resource_store: ResourceStore,
    _fake_sr,
    stale_on_disk: bool,
    expect_upscaled: int,
    expect_skipped: int,
):
    """存量不一致行经 UPSCALE 自愈: 跳过只在记录所指的字节仍在盘上时成立."""

    baseline = await _seed_upscaled(resource_store, _URL, stale_on_disk=stale_on_disk)
    calls = {"n": 0}
    real_run_sr = upscale_mod.run_SR

    async def counting_run_sr(*args, **kwargs):
        calls["n"] += 1
        return await real_run_sr(*args, **kwargs)

    upscale_mod.run_SR = counting_run_sr
    try:
        result = await UpscaleHandler(resource_store, HotSettings()).handle(UpscalePayload())
    finally:
        upscale_mod.run_SR = real_run_sr

    assert result.result is not None
    assert result.result.upscaled == expect_upscaled
    assert result.result.skipped == expect_skipped
    assert result.result.failed == 0
    assert calls["n"] == expect_upscaled  # 标记所指的字节仍在盘上时不启动超分

    record = await resource_store.get_by_url(_LOCATOR)
    assert record is not None
    full = resource_store.full_path(record)
    assert full.read_bytes() == baseline  # 重跑写出超分结果 / 原样保留
    assert record.content_hash == ResourceStore._hash_file(full)  # 记录指向盘上那份
    assert record.size == full.stat().st_size
    assert record.meta is not None and "sr" in record.meta


@pytest.mark.asyncio
async def test_skips_high_resolution(resource_store: ResourceStore, _fake_sr):
    await _seed_image(resource_store, "https://s/big.jpg", (2000, 1346))  # 最长边 2000 ≥ 阈值 1200
    handler = UpscaleHandler(resource_store, HotSettings())
    result = await handler.handle(UpscalePayload())
    assert result.result is not None
    assert result.result.upscaled == 0
    assert result.result.skipped == 1


@pytest.mark.asyncio
async def test_empty_store(resource_store: ResourceStore):
    handler = UpscaleHandler(resource_store, HotSettings())
    result = await handler.handle(UpscalePayload())
    assert result.result is not None
    assert result.result.scanned == 0
    assert result.result.upscaled == 0


@pytest.mark.asyncio
async def test_limit_caps_batch(resource_store: ResourceStore, _fake_sr):
    for i in range(3):
        await _seed_image(resource_store, f"https://s/img{i}.jpg", (800, 538))
    handler = UpscaleHandler(resource_store, HotSettings())
    result = await handler.handle(UpscalePayload(limit=2))
    assert result.result is not None
    assert result.result.upscaled == 2  # 上限 2
