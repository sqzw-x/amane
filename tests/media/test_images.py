"""测试海报裁剪与图像判定纯函数 (图片下载已统一由 ResourceStore 承担)"""

from typing import TYPE_CHECKING

import pytest
from PIL import Image

from amane.enums import WatermarkCorner, WatermarkKind
from amane.media import (
    apply_cover_watermarks,
    apply_cover_watermarks_from_summary,
    crop_box,
    crop_poster,
    needs_upscale,
    probe_size,
    should_crop_poster,
    validate_crop_box,
)
from amane.media.images import _stamp_stems
from amane.parsing import FilePhaseSummary, Mosaic

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
        assert apply_cover_watermarks(dest, has_subtitle=False, uncensored=False, mosaics=(), definition=None) is False
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
                mosaics=(Mosaic.CRACKED,),
                definition="4K",
            )
            is True
        )
        assert dest.read_bytes() != before
        with Image.open(dest) as img:
            assert img.size == (200, 280)

    def test_skips_when_definition_has_no_stamp(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        before = dest.read_bytes()
        assert (
            apply_cover_watermarks(dest, has_subtitle=False, uncensored=False, mosaics=(), definition="1080p") is False
        )
        assert dest.read_bytes() == before

    def test_user_stamp_overrides_builtin(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")
        assert apply_cover_watermarks(
            dest, has_subtitle=True, uncensored=False, mosaics=(), definition=None, watermark_dir=user_dir
        )
        with Image.open(dest) as img:
            pixel = img.convert("RGB").getpixel((6, 6))
            assert isinstance(pixel, tuple) and len(pixel) >= 3
            assert pixel[0] > 200 and pixel[1] < 50 and pixel[2] < 50

    def test_corrupt_user_stamp_falls_back_to_builtin(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        before = dest.read_bytes()
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        (user_dir / "subtitle.png").write_bytes(b"not a png")
        assert apply_cover_watermarks(
            dest, has_subtitle=True, uncensored=False, mosaics=(), definition=None, watermark_dir=user_dir
        )
        assert dest.read_bytes() != before

    def test_missing_cover_returns_false(self, tmp_path: Path):
        assert (
            apply_cover_watermarks(
                tmp_path / "missing.jpg", has_subtitle=True, uncensored=False, mosaics=(), definition=None
            )
            is False
        )

    def test_scale_shrinks_stamp(self, tmp_path: Path):
        """图高 × scale 为角标高度; 大 scale 覆盖到的像素在小 scale 下仍是底色."""
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

        def paint(scale: float) -> Path:
            dest = tmp_path / f"cover-{scale}.jpg"
            Image.new("RGB", (800, 538), "blue").save(dest)
            apply_cover_watermarks(
                dest,
                has_subtitle=True,
                uncensored=False,
                mosaics=(),
                definition=None,
                watermark_dir=user_dir,
                scale=scale,
            )
            return dest

        big = Image.open(paint(0.2)).convert("RGB")
        small = Image.open(paint(0.08)).convert("RGB")
        probe = (40, 80)
        big_px = big.getpixel(probe)
        small_px = small.getpixel(probe)
        assert isinstance(big_px, tuple) and isinstance(small_px, tuple)
        assert big_px[0] > 180 and big_px[2] < 80
        assert small_px[2] > 180 and small_px[0] < 80

    def test_same_height_keeps_stamp_size(self, tmp_path: Path):
        """等高校宽不同的封面/海报, 角标高度相同."""
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")

        def paint(name: str, size: tuple[int, int]) -> Image.Image:
            dest = tmp_path / f"{name}.jpg"
            Image.new("RGB", size, "blue").save(dest)
            apply_cover_watermarks(
                dest,
                has_subtitle=True,
                uncensored=False,
                mosaics=(),
                definition=None,
                watermark_dir=user_dir,
                scale=0.2,
            )
            return Image.open(dest).convert("RGB")

        thumb = paint("thumb", (800, 538))
        poster = paint("poster", (376, 538))
        # 538 * 0.2 ≈ 108, pad ≈ 13 → y=20 在角标内; y=160 在角标外
        for img in (thumb, poster):
            inside = img.getpixel((20, 20))
            outside = img.getpixel((20, 160))
            assert isinstance(inside, tuple) and isinstance(outside, tuple)
            assert inside[0] > 180 and inside[2] < 80
            assert outside[2] > 180 and outside[0] < 80
        thumb.close()
        poster.close()

    def test_corner_top_right(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (400, 300), "blue").save(dest)
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")
        apply_cover_watermarks(
            dest,
            has_subtitle=True,
            uncensored=False,
            mosaics=(),
            definition=None,
            watermark_dir=user_dir,
            scale=0.2,
            corners={WatermarkKind.SUBTITLE: WatermarkCorner.TOP_RIGHT},
        )
        with Image.open(dest) as img:
            rgb = img.convert("RGB")
            left = rgb.getpixel((12, 20))
            right = rgb.getpixel((380, 30))
            assert isinstance(left, tuple) and isinstance(right, tuple)
            assert left[2] > 180 and left[0] < 80
            assert right[0] > 180 and right[2] < 80

    def test_corner_bottom_left(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (400, 300), "blue").save(dest)
        user_dir = tmp_path / "watermarks"
        user_dir.mkdir()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(user_dir / "subtitle.png")
        apply_cover_watermarks(
            dest,
            has_subtitle=True,
            uncensored=False,
            mosaics=(),
            definition=None,
            watermark_dir=user_dir,
            scale=0.2,
            corners={WatermarkKind.SUBTITLE: WatermarkCorner.BOTTOM_LEFT},
        )
        with Image.open(dest) as img:
            rgb = img.convert("RGB")
            top = rgb.getpixel((20, 20))
            bottom = rgb.getpixel((20, 280))
            assert isinstance(top, tuple) and isinstance(bottom, tuple)
            assert top[2] > 180 and top[0] < 80
            assert bottom[0] > 180 and bottom[2] < 80


class TestStampStems:
    """mosaics 是同一 Metadata 下多个文件的聚合, 可同时含多个取值."""

    @pytest.mark.parametrize(
        ("has_subtitle", "uncensored", "mosaics", "definition", "expected", "desc"),
        [
            (False, False, (), None, [], "无任何相位"),
            (True, False, (), None, ["subtitle"], "仅中字"),
            (False, True, (), None, ["uncensored"], "仅无码"),
            (False, False, (Mosaic.CRACKED,), None, ["cracked"], "仅破解"),
            (False, False, (Mosaic.LEAKED,), None, ["leaked"], "仅流出"),
            (
                False,
                False,
                (Mosaic.CRACKED, Mosaic.LEAKED),
                None,
                ["cracked", "leaked"],
                "破解与流出并存时各贴一枚",
            ),
            (
                True,
                True,
                (Mosaic.UNCENSORED,),
                "4K",
                ["subtitle", "uncensored", "4k"],
                "UNCENSORED 只经 uncensored 位反映, 不额外出主干",
            ),
            (True, False, (), "1080P", ["subtitle", "1080p"], "清晰度主干转小写"),
        ],
    )
    def test_stems(self, has_subtitle, uncensored, mosaics, definition, expected, desc):
        stems = _stamp_stems(has_subtitle=has_subtitle, uncensored=uncensored, mosaics=mosaics, definition=definition)
        assert stems == expected, desc


class TestCoverWatermarksFromSummary:
    """库路径封面按 Metadata 共用一份, 角标取全部文件的聚合相位."""

    def test_empty_summary_is_noop(self, tmp_path: Path):
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        before = dest.read_bytes()
        assert apply_cover_watermarks_from_summary(dest, FilePhaseSummary()) is False
        assert dest.read_bytes() == before

    def test_union_of_versions_paints_every_marker(self, tmp_path: Path):
        """原版 + 中字 + 无码三个文件聚合后, 单张封面须同时带中字与无码角标."""
        dest = tmp_path / "cover.jpg"
        Image.new("RGB", (200, 280), "blue").save(dest)
        summary = FilePhaseSummary(has_subtitle=True, uncensored=True, mosaics=(Mosaic.UNCENSORED,))
        assert (
            apply_cover_watermarks_from_summary(
                dest,
                summary,
                corners={
                    WatermarkKind.SUBTITLE: WatermarkCorner.TOP_LEFT,
                    WatermarkKind.UNCENSORED: WatermarkCorner.TOP_RIGHT,
                },
            )
            is True
        )
        with Image.open(dest) as img:
            pixels = img.convert("RGB").load()
            assert pixels is not None

            def painted(x0: int, y0: int, x1: int, y1: int) -> int:
                # JPEG 有损, 以阈值判定是否仍为底色; 角标为圆角图形, 只统计区域内数量.
                n = 0
                for x in range(x0, x1):
                    for y in range(y0, y1):
                        r, g, b = pixels[x, y]
                        if not (r < 16 and g < 16 and b > 239):
                            n += 1
                return n

            # 中字贴左上, 无码贴右上, 两侧都须着色; 下半幅保持底色.
            assert painted(0, 0, 100, 40) > 0
            assert painted(100, 0, 200, 40) > 0
            assert painted(0, 240, 200, 280) == 0

    def test_missing_cover_returns_false(self, tmp_path: Path):
        summary = FilePhaseSummary(has_subtitle=True)
        assert apply_cover_watermarks_from_summary(tmp_path / "missing.jpg", summary) is False
