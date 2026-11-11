"""封面角标 PNG: `{data_dir}/watermarks/{stem}.png` 同名则覆盖包内文件.

文件名与 FileInfo 相位对齐 (全小写): subtitle / uncensored / cracked / leaked,
以及分辨率 `definition.casefold()` (内置覆盖 DEFINITION_VALUES 的每一档).
合并档位前的旧名仍在 `DEFINITION_ALIASES` 里: 用户目录整层优先, 层内先规范名再旧名,
因此只替换了 `sd.png` 的用户不会被内置的 `480p.png` 抢先生效.
缺文件则跳过该枚, 不回退文字. 有码 / VR / 3D 无对应相位, 不内置.
"""

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

import structlog
from PIL import Image

logger = structlog.get_logger()

USER_DIRNAME = "watermarks"

#: 合并档位前的水印名, 只在查找时作为后备; 写入与文档都以 DEFINITION_VALUES 为准.
DEFINITION_ALIASES: dict[str, tuple[str, ...]] = {"480p": ("sd",), "720p": ("hd",)}

_STEM_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def user_watermark_dir(data_dir: Path) -> Path:
    return data_dir / USER_DIRNAME


def is_stamp_stem(stem: str) -> bool:
    return bool(stem) and all(ch in _STEM_CHARS for ch in stem)


def load_stamp(stem: str, user_dir: Path | None, aliases: Sequence[str] = ()) -> Image.Image | None:
    """用户文件优先; 损坏则回退内置; 都没有则 None. 别名只在本层内生效."""
    names = [name for name in (stem, *aliases) if is_stamp_stem(name)]
    if not names:
        return None
    if user_dir is not None:
        for name in names:
            user = user_dir / f"{name}.png"
            if user.is_file():
                loaded = _open_rgba(user)
                if loaded is not None:
                    return loaded
                logger.warning("user watermark unreadable, falling back", path=str(user))
    for name in names:
        builtin = _load_builtin(f"{name}.png")
        if builtin is not None:
            return builtin
    return None


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
