"""测试海报裁剪与图像判定纯函数 (图片下载已统一由 ResourceStore 承担)"""

from typing import TYPE_CHECKING

import pytest
from PIL import Image

from amane.media import (
    apply_cover_watermarks,
    crop_box,
    crop_poster,
    format_crop_box_args,
    needs_upscale,
    probe_size,
    should_crop_poster,
    validate_crop_box,
)
from amane.parsing import Mosaic

if TYPE_CHECKING:
    from pathlib import Path


class TestCropPoster:
    def test_crop_poster_from_thumb(self, tmp_path: Path):
        """裁剪缩略图右侧生成海报 (默认宽高比 0.7)"""
        img = Image.new("RGB", (800, 538), color="blue")
        thumb_path = tmp_path / "thumb.jpg"
        img.save(thumb_path)

        poster_path = tmp_path / "poster.jpg"
        result = crop_poster(thumb_path, poster_path, poster_ratio=0.7)

        assert result is True
        poster = Image.open(poster_path)
        # int(538 * 0.7) = 376, 靠右: left = 800 - 376 = 424
        assert poster.size == (376, 538)

    def test_crop_poster_missing_source_returns_false(self, tmp_path: Path):
        result = crop_poster(tmp_path / "nonexistent.jpg", tmp_path / "poster.jpg")
        assert result is False


class TestValidateCropBox:
    @pytest.mark.parametrize(
        ("box", "size", "expected", "desc"),
        [
            ((100, 0, 400, 538), (800, 538), True, "合法子矩形"),
            ((0, 0, 800, 538), (800, 538), True, "整图"),
            ((0, 0, 0, 538), (800, 538), False, "零宽"),
            ((0, 0, 800, 0), (800, 538), False, "零高"),
            ((400, 0, 100, 538), (800, 538), False, "left>=right"),
            ((0, 400, 800, 100), (800, 538), False, "top>=bottom"),
            ((-1, 0, 100, 100), (800, 538), False, "left 越界"),
            ((0, -1, 100, 100), (800, 538), False, "top 越界"),
            ((0, 0, 801, 538), (800, 538), False, "right 越界"),
            ((0, 0, 800, 539), (800, 538), False, "bottom 越界"),
            ((0, 0, 10, 10), (0, 538), False, "图像宽非法"),
        ],
    )
    def test_validate(self, box, size, expected, desc):
        assert validate_crop_box(box, size) is expected, desc


class TestFormatCropBoxArgs:
    def test_format(self):
        assert format_crop_box_args(10, 20, 300, 400) == "box:10,20,300,400"


class TestCropBox:
    def test_crops_region(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        Image.new("RGB", (800, 538), "blue").save(src)
        dest = tmp_path / "out.jpg"
        assert crop_box(src, dest, (421, 0, 800, 538)) is True
        out = Image.open(dest)
        assert out.size == (379, 538)

    def test_full_image(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        Image.new("RGB", (100, 200), "red").save(src)
        dest = tmp_path / "out.jpg"
        assert crop_box(src, dest, (0, 0, 100, 200)) is True
        assert Image.open(dest).size == (100, 200)

    @pytest.mark.parametrize(
        ("box", "desc"),
        [
            ((0, 0, 0, 100), "零面积"),
            ((0, 0, 900, 538), "越界"),
            ((500, 0, 100, 538), "反向"),
        ],
    )
    def test_invalid_returns_false(self, tmp_path: Path, box, desc):
        src = tmp_path / "src.jpg"
        Image.new("RGB", (800, 538), "blue").save(src)
        assert crop_box(src, tmp_path / "out.jpg", box) is False, desc

    def test_missing_source_returns_false(self, tmp_path: Path):
        assert crop_box(tmp_path / "missing.jpg", tmp_path / "out.jpg", (0, 0, 10, 10)) is False


class TestProbeSize:
    def test_reads_size(self, tmp_path: Path):
        p = tmp_path / "img.jpg"
        Image.new("RGB", (800, 538), "red").save(p)
        assert probe_size(p) == (800, 538)

    @pytest.mark.parametrize(
        ("setup", "desc"),
        [
            ("missing", "文件不存在"),
            ("empty", "零字节文件"),
            ("garbage", "非图像内容"),
        ],
    )
    def test_invalid_returns_none(self, tmp_path: Path, setup: str, desc: str):
        p = tmp_path / "x.jpg"
        if setup == "empty":
            p.write_bytes(b"")
        elif setup == "garbage":
            p.write_bytes(b"not an image at all")
        # missing: 不创建文件
        assert probe_size(p) is None, desc


class TestShouldCropPoster:
    @pytest.mark.parametrize(
        ("thumb", "candidate", "skip", "expected", "desc"),
        [
            (None, (300, 420), 0.9, False, "无 thumb 无法裁"),
            ((800, 538), None, 0.9, True, "无候选 → 从 thumb 裁"),
            ((800, 538), (300, 420), 0.9, True, "候选偏矮 (0.78<0.9) → 裁"),
            ((800, 538), (379, 530), 0.9, False, "候选够高 (0.985≥0.9) → 不裁"),
            ((800, 538), (379, 538), 0.9, False, "候选等高 → 不裁"),
            ((800, 0), (300, 420), 0.9, False, "thumb 高非法 → 不裁"),
            ((800, 538), (300, 0), 0.9, False, "候选高非法 → 不裁"),
            ((800, 538), (300, 484), 0.9, True, "边界 0.899<0.9 → 裁"),
        ],
    )
    def test_decision(self, thumb, candidate, skip, expected, desc):
        assert should_crop_poster(thumb, candidate, skip_ratio=skip) is expected, desc


class TestNeedsUpscale:
    @pytest.mark.parametrize(
        ("size", "file_bytes", "max_dim", "max_bytes", "expected", "desc"),
        [
            ((800, 538), 100_000, 1200, 500_000, True, "小图小文件 → 超分"),
            ((1600, 1076), 100_000, 1200, 500_000, False, "最长边已达标 → 不超分"),
            ((800, 538), 600_000, 1200, 500_000, False, "文件过大视为够清晰 → 不超分"),
            ((1200, 800), 100_000, 1200, 500_000, False, "恰等阈值 (不<) → 不超分"),
            ((1199, 800), 100_000, 1200, 500_000, True, "略低于阈值 → 超分"),
            (None, 100_000, 1200, 500_000, False, "尺寸未知 → 不超分"),
            ((800, 538), 500_000, 1200, 500_000, True, "文件恰等字节阈值 (≤) → 超分"),
        ],
    )
    def test_decision(self, size, file_bytes, max_dim, max_bytes, expected, desc):
        assert needs_upscale(size, file_bytes, max_dim_threshold=max_dim, max_bytes_threshold=max_bytes) is expected, (
            desc
        )


class TestCoverWatermarks:
    def test_noop_when_no_markers(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        before = dest.read_bytes()
        assert apply_cover_watermarks(dest, has_subtitle=False, uncensored=False, mosaic=None, definition=None) is False
        assert dest.read_bytes() == before

    def test_paints_when_marked(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        before = dest.read_bytes()
        assert (
            apply_cover_watermarks(
                dest,
                has_subtitle=True,
                uncensored=True,
                mosaic=Mosaic.CRACKED,
                definition="4K",
            )
            is True
        )
        assert dest.read_bytes() != before
        with Image.open(dest) as img:
            assert img.size == (200, 280)
