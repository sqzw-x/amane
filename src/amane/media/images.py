"""海报裁剪与封面角标. 判定函数只依赖尺寸与阈值, 无 I/O."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import structlog
from PIL import Image

from ..enums import WatermarkCorner, WatermarkKind
from ..parsing import FileInfo, FilePhaseSummary, Mosaic, file_shows_uncensored
from .watermarks import load_stamp

logger = structlog.get_logger()

# 标准 DVD 封面约 800x538, 海报为右侧约 379x538 (≈0.704); 默认取 0.7.
_DEFAULT_POSTER_RATIO = 0.7
_DEFAULT_JPEG_QUALITY = 95

# 与自动右侧比 `0.7000` 共存于 op=crop.
CROP_BOX_ARGS_PREFIX = "box:"


def format_crop_box_args(left: int, top: int, right: int, bottom: int) -> str:
    return f"{CROP_BOX_ARGS_PREFIX}{left},{top},{right},{bottom}"


def validate_crop_box(box: tuple[int, int, int, int], image_size: tuple[int, int]) -> bool:
    left, top, right, bottom = box
    w, h = image_size
    if w <= 0 or h <= 0:
        return False
    return left >= 0 and top >= 0 and right <= w and bottom <= h and left < right and top < bottom


def probe_size(path: Path) -> tuple[int, int] | None:
    # 损坏 / 非图像返回 None, 不抛异常.
    try:
        with Image.open(path) as img:
            return img.size
    except Exception as e:
        logger.debug("probe_size failed", path=str(path), error=str(e))
        return None


def should_crop_poster(
    thumb_size: tuple[int, int] | None, candidate_size: tuple[int, int] | None, *, skip_ratio: float = 0.9
) -> bool:
    """无 thumb 尺寸则 False. 无 poster 候选则 True.

    候选已足够高 (b/h ≥ skip_ratio) 则不裁剪: 裁剪有错位风险.
    """
    if thumb_size is None:
        return False
    if candidate_size is None:
        return True
    h = thumb_size[1]
    b = candidate_size[1]
    if h <= 0 or b <= 0:
        return False
    return (b / h) < skip_ratio


def needs_upscale(
    size: tuple[int, int] | None,
    file_bytes: int,
    *,
    max_dim_threshold: int,
    max_bytes_threshold: int,
) -> bool:
    """最长边 < max_dim_threshold 且 文件大小 ≤ max_bytes_threshold. 无法读取尺寸时不超分."""
    if size is None:
        return False
    if file_bytes > max_bytes_threshold:
        return False
    return max(size) < max_dim_threshold


def crop_poster(
    thumb_path: Path,
    poster_path: Path,
    *,
    poster_ratio: float = _DEFAULT_POSTER_RATIO,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> bool:
    """海报取缩略图右侧, 宽度 = height × poster_ratio."""
    try:
        img = Image.open(thumb_path)
        w, h = img.size

        target_w = int(h * poster_ratio)
        if target_w >= w:
            # 已足够窄, 整图作为海报.
            img.save(poster_path, quality=jpeg_quality)
            return True

        # 从右侧裁剪.
        left = w - target_w
        cropped = img.crop((left, 0, w, h))
        poster_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(poster_path, quality=jpeg_quality)
        return True
    except Exception as e:
        logger.warning("poster crop failed", path=str(thumb_path), error=str(e))
        return False


def crop_box(
    src_path: Path,
    dest_path: Path,
    box: tuple[int, int, int, int],
    *,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> bool:
    """框须落在图像范围内且面积为正 (见 ``validate_crop_box``)."""
    try:
        img = Image.open(src_path)
        if not validate_crop_box(box, img.size):
            logger.warning(
                "crop box invalid",
                path=str(src_path),
                box=box,
                size=img.size,
            )
            return False
        cropped = img.crop(box)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(dest_path, quality=jpeg_quality)
        return True
    except Exception as e:
        logger.warning("box crop failed", path=str(src_path), error=str(e))
        return False


# 与目标比差异小于该值时不改写, 避免无谓的再编码.
_ASPECT_TOLERANCE = 0.005


def normalize_poster_aspect(
    path: Path,
    *,
    target_ratio: float = _DEFAULT_POSTER_RATIO,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> bool:
    """把库路径海报居中裁剪到 target_ratio; 只裁两侧, 不补边, 不放大.

    Emby / Jellyfin 以固定宽高比渲染海报, 偏宽的图由客户端裁掉两侧, 叠在上角的
    角标会连同边距一起被切除. 落盘时归一化可使客户端无需裁剪.
    比目标窄的图无法通过裁剪达到目标比, 保持原样.
    """
    if target_ratio <= 0:
        return False
    try:
        with Image.open(path) as src:
            img = src.convert("RGB")
        width, height = img.size
        if width <= 0 or height <= 0:
            return False
        if width / height - target_ratio <= _ASPECT_TOLERANCE:
            return False
        new_width = max(1, min(width, round(height * target_ratio)))
        if new_width >= width:
            return False
        left = (width - new_width) // 2
        img.crop((left, 0, left + new_width, height)).save(path, quality=jpeg_quality)
        return True
    except Exception as e:
        logger.warning("poster aspect normalize failed", path=str(path), error=str(e))
        return False


# 按图高缩放, 海报与封面同高时一样大.
_DEFAULT_SCALE = 0.08
_SCALE_MIN = 0.03
_SCALE_MAX = 0.25
_STAMP_MIN_HEIGHT = 16

_FIXED_KINDS: frozenset[str] = frozenset(
    (WatermarkKind.SUBTITLE, WatermarkKind.UNCENSORED, WatermarkKind.CRACKED, WatermarkKind.LEAKED)
)
_RIGHT_CORNERS = frozenset((WatermarkCorner.TOP_RIGHT, WatermarkCorner.BOTTOM_RIGHT))
_BOTTOM_CORNERS = frozenset((WatermarkCorner.BOTTOM_LEFT, WatermarkCorner.BOTTOM_RIGHT))


def _stamp_stems(
    *,
    has_subtitle: bool,
    uncensored: bool,
    mosaics: Sequence[Mosaic],
    definition: str | None,
) -> list[str]:
    """相位 → PNG 主干, 顺序: 中字 / 无码 / 破解 / 流出 / 清晰度.

    mosaics 聚合同一 Metadata 的多个文件时可同时含破解与流出, 各贴一枚.
    """
    stems: list[str] = []
    if has_subtitle:
        stems.append("subtitle")
    if uncensored:
        stems.append("uncensored")
    if Mosaic.CRACKED in mosaics:
        stems.append("cracked")
    if Mosaic.LEAKED in mosaics:
        stems.append("leaked")
    if definition:
        stems.append(definition.casefold())
    return stems


def _stamp_kind(stem: str) -> WatermarkKind:
    if stem in _FIXED_KINDS:
        return WatermarkKind(stem)
    return WatermarkKind.DEFINITION


def _corner_for(stem: str, corners: Mapping[WatermarkKind, WatermarkCorner] | None) -> WatermarkCorner:
    kind = _stamp_kind(stem)
    if corners is None:
        return WatermarkCorner.TOP_LEFT
    return corners.get(kind, WatermarkCorner.TOP_LEFT)


def _fit_stamp(stamp: Image.Image, target_h: int) -> Image.Image | None:
    """裁掉透明边再按高度缩放. 无可见像素则跳过."""
    rgba = stamp.convert("RGBA")
    bbox = rgba.getbbox()
    if bbox is None:
        return None
    cropped = rgba.crop(bbox)
    width, height = cropped.size
    if height <= 0 or width <= 0:
        return None
    new_h = target_h
    new_w = max(1, round(width * new_h / height))
    return cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _paste_stamps(
    img: Image.Image,
    stamps_by_corner: dict[WatermarkCorner, list[Image.Image]],
    *,
    pad: int,
    gap: int,
) -> None:
    """同角按列表顺序向内叠: 上角往下, 下角往上; 右角右对齐."""
    width, height = img.size
    for corner, stamps in stamps_by_corner.items():
        if not stamps:
            continue
        right = corner in _RIGHT_CORNERS
        if corner in _BOTTOM_CORNERS:
            y = height - pad
            for stamp in stamps:
                stamp_w, stamp_h = stamp.size
                y -= stamp_h
                x = width - pad - stamp_w if right else pad
                img.paste(stamp, (x, y), stamp)
                y -= gap
        else:
            y = pad
            for stamp in stamps:
                stamp_w, stamp_h = stamp.size
                x = width - pad - stamp_w if right else pad
                img.paste(stamp, (x, y), stamp)
                y += stamp_h + gap


def apply_cover_watermarks(
    path: Path,
    *,
    has_subtitle: bool,
    uncensored: bool,
    mosaics: Sequence[Mosaic],
    definition: str | None,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
    watermark_dir: Path | None = None,
    scale: float = _DEFAULT_SCALE,
    corners: Mapping[WatermarkKind, WatermarkCorner] | None = None,
) -> bool:
    """叠到库路径封面/海报. 无标记或无图则不动. 不修改 Resource 原图."""
    stems = _stamp_stems(has_subtitle=has_subtitle, uncensored=uncensored, mosaics=mosaics, definition=definition)
    if not stems:
        return False
    try:
        with Image.open(path) as src:
            img = src.convert("RGBA")
        width, height = img.size
        if width <= 0 or height <= 0:
            return False
        ratio = min(_SCALE_MAX, max(_SCALE_MIN, scale))
        target_h = max(_STAMP_MIN_HEIGHT, round(height * ratio))
        pad = max(4, target_h // 8)
        gap = max(4, target_h // 10)
        grouped: dict[WatermarkCorner, list[Image.Image]] = {corner: [] for corner in WatermarkCorner}
        for stem in stems:
            raw = load_stamp(stem, watermark_dir)
            if raw is None:
                continue
            fitted = _fit_stamp(raw, target_h)
            if fitted is None:
                continue
            grouped[_corner_for(stem, corners)].append(fitted)
        if not any(grouped.values()):
            return False
        _paste_stamps(img, grouped, pad=pad, gap=gap)
        rgb = img.convert("RGB")
        rgb.save(path, quality=jpeg_quality)
        return True
    except Exception as e:
        logger.warning("cover watermark failed", path=str(path), error=str(e))
        return False


def apply_cover_watermarks_from_info(
    path: Path,
    info: FileInfo,
    *,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
    watermark_dir: Path | None = None,
    scale: float = _DEFAULT_SCALE,
    corners: Mapping[WatermarkKind, WatermarkCorner] | None = None,
) -> bool:
    return apply_cover_watermarks(
        path,
        has_subtitle=info.has_subtitle,
        uncensored=file_shows_uncensored(info.mosaic, info.content_type),
        mosaics=() if info.mosaic is None else (info.mosaic,),
        definition=info.definition,
        jpeg_quality=jpeg_quality,
        watermark_dir=watermark_dir,
        scale=scale,
        corners=corners,
    )


def apply_cover_watermarks_from_summary(
    path: Path,
    summary: FilePhaseSummary,
    *,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
    watermark_dir: Path | None = None,
    scale: float = _DEFAULT_SCALE,
    corners: Mapping[WatermarkKind, WatermarkCorner] | None = None,
) -> bool:
    """按同一 Metadata 下全部文件的聚合相位加水印.

    库路径封面按 Metadata 共用一份, 单个文件的相位不足以描述它.
    """
    return apply_cover_watermarks(
        path,
        has_subtitle=summary.has_subtitle,
        uncensored=summary.uncensored,
        mosaics=summary.mosaics,
        definition=summary.definition,
        jpeg_quality=jpeg_quality,
        watermark_dir=watermark_dir,
        scale=scale,
        corners=corners,
    )
