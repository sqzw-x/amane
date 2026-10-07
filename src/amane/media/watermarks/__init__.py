"""封面角标 PNG: `{data_dir}/watermarks/{stem}.png` 同名则覆盖包内文件.

文件名与 FileInfo 相位对齐 (全小写): subtitle / uncensored / cracked / leaked,
以及分辨率 `definition.casefold()` (内置仅 4k / 8k; 用户可放置 1080p.png 等).
缺文件则跳过该枚, 不回退文字. 有码 / VR / 3D 无对应相位, 不内置.
"""

from importlib.resources import files
from pathlib import Path

import structlog
from PIL import Image

logger = structlog.get_logger()

USER_DIRNAME = "watermarks"

_STEM_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def user_watermark_dir(data_dir: Path) -> Path:
    return data_dir / USER_DIRNAME


def is_stamp_stem(stem: str) -> bool:
    return bool(stem) and all(ch in _STEM_CHARS for ch in stem)


def load_stamp(stem: str, user_dir: Path | None) -> Image.Image | None:
    # 用户文件优先; 损坏则回退内置; 都没有则 None.
    if not is_stamp_stem(stem):
        return None
    name = f"{stem}.png"
    if user_dir is not None:
        user = user_dir / name
        if user.is_file():
            loaded = _open_rgba(user)
            if loaded is not None:
                return loaded
            logger.warning("user watermark unreadable, falling back", path=str(user))
    return _load_builtin(name)


def _load_builtin(name: str) -> Image.Image | None:
    ref = files(__name__).joinpath(name)
    if not ref.is_file():
        return None
    try:
        with ref.open("rb") as fh, Image.open(fh) as img:
            return img.convert("RGBA")
    except Exception as e:
        logger.warning("builtin watermark unreadable", name=name, error=str(e))
        return None


def _open_rgba(path: Path) -> Image.Image | None:
    try:
        with Image.open(path) as img:
            return img.convert("RGBA")
    except Exception as e:
        logger.warning("watermark open failed", path=str(path), error=str(e))
        return None
