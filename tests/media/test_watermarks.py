"""用户覆盖目录优先于包内置; 非法主干拒绝."""

from typing import TYPE_CHECKING

import pytest
from PIL import Image

from amane.media.watermarks import is_stamp_stem, load_stamp
from amane.parsing import DEFINITION_VALUES

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("subtitle", True),
        ("4k", True),
        ("1080p", True),
        ("", False),
        ("subtitle.png", False),
        ("../subtitle", False),
        ("sub/title", False),
        ("SUBTITLE", False),
        ("4K", False),
    ],
)
def test_is_stamp_stem(stem: str, expected: bool) -> None:
    assert is_stamp_stem(stem) is expected


def test_load_builtin_subtitle() -> None:
    img = load_stamp("subtitle", None)
    assert img is not None
    assert img.mode == "RGBA"
    assert img.getbbox() is not None


@pytest.mark.parametrize("stem", [value.casefold() for value in DEFINITION_VALUES])
def test_load_builtin_definition(stem: str) -> None:
    """每个分辨率档都要有内置角标: 缺一枚就静默不画."""
    img = load_stamp(stem, None)
    assert img is not None
    assert img.getbbox() is not None


def test_load_unknown_stem_without_user_file() -> None:
    assert load_stamp("bogus", None) is None


@pytest.mark.parametrize("stem", ["subtitle", "1080p"])
def test_user_file_wins(stem: str, tmp_path: Path) -> None:
    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (8, 8), (0, 255, 0, 255)).save(user_dir / f"{stem}.png")
    img = load_stamp(stem, user_dir)
    assert img is not None
    pixel = img.getpixel((0, 0))
    assert pixel == (0, 255, 0, 255)


def test_corrupt_user_falls_back(tmp_path: Path) -> None:
    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    (user_dir / "subtitle.png").write_bytes(b"garbage")
    img = load_stamp("subtitle", user_dir)
    assert img is not None
    assert img.getbbox() is not None


def test_rejects_path_stem(tmp_path: Path) -> None:
    user_dir = tmp_path / "watermarks"
    user_dir.mkdir()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(user_dir / "subtitle.png")
    assert load_stamp("../watermarks/subtitle", user_dir) is None
    assert load_stamp("subtitle.png", user_dir) is None
