#!/usr/bin/env python3
"""Rasterize assets/logo.svg into the packaged icon files.

Source of truth is assets/logo.svg. This writes:

- web/public/favicon.svg (byte-identical copy)
- assets/app.ico (Windows exe + tray)
- assets/app.icns (macOS .app; requires iconutil)
- androidapp/.../mipmap-*/ic_launcher_foreground.png (Android launcher icon)

rsvg-convert (librsvg) is required. iconutil is macOS-only; other
platforms still produce favicon + ico.
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "logo.svg"
FAVICON = ROOT / "web" / "public" / "favicon.svg"
ICO = ROOT / "assets" / "app.ico"
ICNS = ROOT / "assets" / "app.icns"

# Windows Explorer + tray. 256 is PNG-in-ICO for the jumbo size.
ICO_SIZES = (16, 24, 32, 48, 64, 256)

# iconutil slots: base size → @1x and @2x pixel sizes.
ICNS_SLOTS = (16, 32, 128, 256, 512)

# Android adaptive icon: the foreground canvas is 108dp, rendered per density.
ANDROID_RES = ROOT / "androidapp" / "app" / "src" / "main" / "res"
ANDROID_DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
ANDROID_ICON_DP = 108
# logo.svg 的原始视口; 前景把视口放宽到 48 单位, 字形因此缩到画布的 2/3 并居中.
# y 方向偏移 1 单位: 字形包围盒中心在 (16 15), 32 单位画布的中心是 (16 16).
LOGO_VIEWBOX = "0 0 32 32"
ANDROID_VIEWBOX = "-8 -9 48 48"


def pack_ico(images: list[tuple[int, bytes]]) -> bytes:
    """Pack PNG blobs into a Vista+ ICO (PNG-in-ICO)."""
    if not images:
        raise ValueError("ICO needs at least one image")
    count = len(images)
    offset = 6 + 16 * count
    entries = bytearray()
    payload = bytearray()
    for size, png in images:
        width = 0 if size >= 256 else size
        height = width
        entries.extend(struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(png), offset))
        payload.extend(png)
        offset += len(png)
    return struct.pack("<HHH", 0, 1, count) + bytes(entries) + bytes(payload)


def _rsvg() -> str:
    path = shutil.which("rsvg-convert")
    if path is None:
        raise SystemExit("rsvg-convert not found (install librsvg)")
    return path


def render_png(rsvg: str, size: int, dest: Path) -> None:
    subprocess.run([rsvg, "-w", str(size), "-h", str(size), "-o", str(dest), str(LOGO)], check=True)


def write_favicon() -> None:
    FAVICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LOGO, FAVICON)


def write_ico(rsvg: str) -> None:
    images: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="amane-ico-") as tmp:
        tmp_path = Path(tmp)
        for size in ICO_SIZES:
            png_path = tmp_path / f"{size}.png"
            render_png(rsvg, size, png_path)
            images.append((size, png_path.read_bytes()))
    ICO.write_bytes(pack_ico(images))


def write_icns(rsvg: str) -> None:
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("iconutil not found; skipping app.icns", file=sys.stderr)  # noqa: T201
        return
    with tempfile.TemporaryDirectory(prefix="amane-icns-") as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for slot in ICNS_SLOTS:
            render_png(rsvg, slot, iconset / f"icon_{slot}x{slot}.png")
            render_png(rsvg, slot * 2, iconset / f"icon_{slot}x{slot}@2x.png")
        subprocess.run([iconutil, "-c", "icns", "-o", str(ICNS), str(iconset)], check=True)


def write_android_foreground(rsvg: str) -> None:
    """Android 自适应图标的前景: 只保留白色字形, 视口留白把它收进安全区.

    自适应图标会被启动器按圆形 / 圆角遮罩裁切 (108dp 画布只有中间 72dp 保证可见), 因此前景不画徽标底色:
    渐变由 `drawable/ic_launcher_background.xml` 铺满整层. 字形中间的播放三角改为**镂空** (遮罩挖洞),
    透出的正是背景层, 与 logo.svg 里三角填品牌渐变的做法一致, 且不必复制品牌色或几何.
    """
    src = LOGO.read_text(encoding="utf-8")
    badge_pattern = r"\s*<rect\b[^>]*fill=\"url\(#g\)\"[^>]*/>"
    counter_pattern = r"<path\b[^>]*fill=\"url\(#g\)\"[^>]*/>"
    counter = re.search(counter_pattern, src)
    if counter is None:
        raise SystemExit("logo.svg changed: the play-triangle counter is missing")
    trimmed = re.sub(badge_pattern, "", src, count=1)
    trimmed, removed = re.subn(counter_pattern, "", trimmed, count=1)
    if removed != 1 or f'viewBox="{LOGO_VIEWBOX}"' not in trimmed:
        raise SystemExit("logo.svg changed: cannot build the Android foreground")

    # 遮罩里的三角与原图同坐标 (作用于白色字形的局部坐标系), 只是改成黑色即挖洞.
    hole = counter.group(0).replace('fill="url(#g)"', 'fill="#000"').replace('stroke="url(#g)"', 'stroke="#000"')
    mask = f'<mask id="glyph-hole"><rect x="-2" y="-2" width="22" height="22" fill="#fff"/>{hole}</mask>'
    glyph, masked = re.subn(r'(<path\b[^>]*fill="#fff")', r'\1 mask="url(#glyph-hole)"', trimmed, count=1)
    if masked != 1:
        raise SystemExit("logo.svg changed: the letterform path is missing")
    scaled = glyph.replace(
        f'viewBox="{LOGO_VIEWBOX}"',
        f'viewBox="{ANDROID_VIEWBOX}"',
        1,
    ).replace("</defs>", f"{mask}</defs>", 1)

    with tempfile.TemporaryDirectory(prefix="amane-android-") as tmp:
        foreground = Path(tmp) / "foreground.svg"
        foreground.write_text(scaled, encoding="utf-8")
        for density, scale in ANDROID_DENSITIES.items():
            dest = ANDROID_RES / f"mipmap-{density}" / "ic_launcher_foreground.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = round(ANDROID_ICON_DP * scale)
            subprocess.run(
                [rsvg, "-w", str(size), "-h", str(size), "-o", str(dest), str(foreground)],
                check=True,
            )


def main() -> int:
    if not LOGO.is_file():
        raise SystemExit(f"missing {LOGO}")
    rsvg = _rsvg()
    write_favicon()
    write_ico(rsvg)
    write_icns(rsvg)
    write_android_foreground(rsvg)
    print(f"FAVICON={FAVICON}")  # noqa: T201
    print(f"ICO={ICO}")  # noqa: T201
    if ICNS.is_file():
        print(f"ICNS={ICNS}")  # noqa: T201
    print(f"ANDROID_MIPMAPS={ANDROID_RES}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
