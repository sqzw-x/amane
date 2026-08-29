"""
海报生成与图像判定.

- `crop_poster`: 从缩略图右侧裁剪生成海报.
- `crop_box`: 按像素框裁剪任意矩形区域.
- 判定纯函数 (`probe_size` / `should_crop_poster` / `needs_upscale` /
  `validate_crop_box`): 仅依赖图像尺寸与基本类型参数, 无 I/O 副作用之外的依赖, 便于表测试.
  阈值由调用方从 config 取出后传入.
(图片下载已统一由 ResourceStore 承担, 见 media/resource_store.py.)
"""

from pathlib import Path

import structlog
from PIL import Image, ImageDraw, ImageFont

from ..parsing import FileInfo, Mosaic, file_shows_uncensored

logger = structlog.get_logger()

# 海报裁剪: 缩略图的右侧部分
# 标准 DVD 封面约 800x538, 海报为右侧约 379x538 (≈0.704); 默认取 0.7
_DEFAULT_POSTER_RATIO = 0.7  # 目标宽高比 (w/h)
_DEFAULT_JPEG_QUALITY = 95

# 手动像素框裁切的派生 args 前缀: `box:L,T,R,B` (与自动右侧比 `0.7000` 共存于 op=crop)
CROP_BOX_ARGS_PREFIX = "box:"


def format_crop_box_args(left: int, top: int, right: int, bottom: int) -> str:
    """手动裁切 → acquire_derived 的 args 串 (`box:L,T,R,B`)."""
    return f"{CROP_BOX_ARGS_PREFIX}{left},{top},{right},{bottom}"


def validate_crop_box(box: tuple[int, int, int, int], image_size: tuple[int, int]) -> bool:
    """校验像素框是否落在图像内且面积为正."""
    left, top, right, bottom = box
    w, h = image_size
    if w <= 0 or h <= 0:
        return False
    return left >= 0 and top >= 0 and right <= w and bottom <= h and left < right and top < bottom


def probe_size(path: Path) -> tuple[int, int] | None:
    """读取图像像素尺寸 (w, h). 损坏/非图像返回 None (不抛异常)."""
    try:
        with Image.open(path) as img:
            return img.size
    except Exception as e:
        logger.debug("probe_size failed", path=str(path), error=str(e))
        return None


def should_crop_poster(
    thumb_size: tuple[int, int] | None, candidate_size: tuple[int, int] | None, *, skip_ratio: float = 0.9
) -> bool:
    """判定是否需要从 thumb 裁剪海报.

     规则:
    - 无 thumb 尺寸 → 无法裁剪, False.
    - 无 poster 候选 → 需要裁剪 (从 thumb 生成), True.
    - 有候选: 若候选已足够高 (b/h ≥ skip_ratio) → 候选本身够用, 不裁剪 (裁剪有错位风险).
       否则候选偏小 → 裁剪 thumb 得到更大海报.
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
    """判定图像是否需要超分.

    需超分 ⟺ 最长边 max(w,h) < max_dim_threshold 且 文件大小 ≤ max_bytes_threshold.
    (大文件视为已够清晰; 无法读取尺寸时不超分.)
    """
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
    """
    裁剪缩略图右侧部分以生成海报.

    标准 JAV 缩略图为横向 (~800x538). 海报取图片右侧, 宽度 = height × poster_ratio
    (封面艺术所在位置).

    成功返回 True, 失败返回 False.
    """
    try:
        img = Image.open(thumb_path)
        w, h = img.size

        # 目标海报宽高比
        target_w = int(h * poster_ratio)
        if target_w >= w:
            # 图片已经足够窄 - 直接使用
            img.save(poster_path, quality=jpeg_quality)
            return True

        # 从右侧裁剪
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
    """按像素框 (left, top, right, bottom) 裁剪图像并保存为 JPEG.

    框须落在图像范围内且面积为正 (见 ``validate_crop_box``). 成功 True, 失败 False.
    """
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


_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
)

# (label, fill RGB) 自上而下: 中字 / 无码 / 破解 / 流出 / 清晰度
_SUBTITLE_BADGE = ("SUB", (220, 90, 40))
_UNCENSORED_BADGE = ("U", (190, 35, 45))
_CRACKED_BADGE = ("CRACK", (120, 50, 160))
_LEAKED_BADGE = ("LEAK", (30, 130, 90))
_DEFINITION_COLOR = (30, 90, 170)


def _badge_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def apply_cover_watermarks(
    path: Path,
    *,
    has_subtitle: bool,
    uncensored: bool,
    mosaic: Mosaic | None,
    definition: str | None,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> bool:
    """在库路径封面/海报左上角叠角标. 无标记则不动. 不改 Resource 原图."""
    badges: list[tuple[str, tuple[int, int, int]]] = []
    if has_subtitle:
        badges.append(_SUBTITLE_BADGE)
    if uncensored:
        badges.append(_UNCENSORED_BADGE)
    if mosaic is Mosaic.CRACKED:
        badges.append(_CRACKED_BADGE)
    elif mosaic is Mosaic.LEAKED:
        badges.append(_LEAKED_BADGE)
    if definition:
        badges.append((definition, _DEFINITION_COLOR))
    if not badges:
        return False
    try:
        with Image.open(path) as src:
            img = src.convert("RGBA")
        width, height = img.size
        if width <= 0 or height <= 0:
            return False
        font_size = max(14, min(width, height) // 16)
        font = _badge_font(font_size)
        draw = ImageDraw.Draw(img)
        pad = max(4, font_size // 5)
        gap = max(4, font_size // 6)
        x = pad
        y = pad
        for label, color in badges:
            left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
            text_w = right - left
            text_h = bottom - top
            box = (x, y, x + text_w + pad * 2, y + text_h + pad * 2)
            draw.rounded_rectangle(box, radius=max(2, pad), fill=(*color, 210))
            draw.text((x + pad - left, y + pad - top), label, font=font, fill=(255, 255, 255, 255))
            y = box[3] + gap
        rgb = img.convert("RGB")
        rgb.save(path, quality=jpeg_quality)
        return True
    except Exception as e:
        logger.warning("cover watermark failed", path=str(path), error=str(e))
        return False


def apply_cover_watermarks_from_info(path: Path, info: FileInfo, *, jpeg_quality: int = _DEFAULT_JPEG_QUALITY) -> bool:
    """按 FileInfo 给库路径封面加水印."""
    return apply_cover_watermarks(
        path,
        has_subtitle=info.has_subtitle,
        uncensored=file_shows_uncensored(info.mosaic, info.content_type),
        mosaic=info.mosaic,
        definition=info.definition,
        jpeg_quality=jpeg_quality,
    )
