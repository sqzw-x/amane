from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class SrTool(StrEnum):
    REALESRGAN = "realesrgan"
    WAIFU2X = "waifu2x"


class SrPreset(StrEnum):
    REALESR_PHOTO_4X = "realesr-photo-4x"
    """Real-ESRGAN 通用照片模型, 4x 原生倍率, 无降噪."""
    WAIFU_PHOTO_2X = "waifu-photo-2x"
    """waifu2x 照片模型, 2x 原生倍率, 无降噪."""


@dataclass(frozen=True, slots=True)
class PresetMeta:
    tool: SrTool
    model: str
    scale: int
    noise_level: int


_PRESET_META: Mapping[SrPreset, PresetMeta] = {
    SrPreset.REALESR_PHOTO_4X: PresetMeta(
        tool=SrTool.REALESRGAN,
        model="realesrgan-x4plus",
        scale=4,
        noise_level=-1,
    ),
    SrPreset.WAIFU_PHOTO_2X: PresetMeta(
        tool=SrTool.WAIFU2X,
        model="models-upconv_7_photo",
        scale=2,
        noise_level=-1,
    ),
}


def get_preset_meta(preset: SrPreset) -> PresetMeta:
    return _PRESET_META[preset]


ANY_ARCH = "any"
"""架构键通配: 该平台上各架构共用同一个包."""


def normalize_arch(machine: str) -> str:
    """``platform.machine()`` → 规范架构名."""
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine.lower(), machine.lower())


@dataclass(frozen=True, slots=True)
class ToolMeta:
    binary_name: str
    default_model: str
    models: tuple[str, ...]
    native_scale: int
    scales: tuple[int, ...]
    download_urls: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """sys.platform → 架构 → 下载 URL. key: darwin/linux/win32."""


_REALESRGAN_RELEASE = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0"

_WAIFU2X_RELEASE = "https://github.com/nihui/waifu2x-ncnn-vulkan/releases/download/20250915"

# 上游的 Linux 包在纯 CPU 模式下段错误 (缺少 Vulkan 设备时的 GPU 初始化, Net::set_vulkan_device,
# 以及 sgemm 卷积路径引用的 Gemm 层未编入), 且不提供 arm64 包. 此处指向修正后的自建包.
_WAIFU2X_SELF_RELEASE = "https://github.com/sqzw-x/waifu2x-ncnn-vulkan/releases/download/amane-linux-1"


_TOOL_META: Mapping[SrTool, ToolMeta] = {
    SrTool.REALESRGAN: ToolMeta(
        binary_name="realesrgan-ncnn-vulkan",
        default_model="realesrgan-x4plus",
        models=(
            "realesrgan-x4plus",
            "realesrgan-x4plus-anime",
            "realesr-animevideov3",
            "realesrnet-x4plus",
        ),
        native_scale=4,
        scales=(2, 3, 4),
        download_urls={
            "darwin": {ANY_ARCH: f"{_REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-macos.zip"},
            "linux": {"x86_64": f"{_REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-ubuntu.zip"},
            "win32": {ANY_ARCH: f"{_REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-windows.zip"},
        },
    ),
    SrTool.WAIFU2X: ToolMeta(
        binary_name="waifu2x-ncnn-vulkan",
        default_model="models-upconv_7_photo",
        models=(
            "models-upconv_7_photo",
            "models-cunet",
            "models-upconv_7_anime_style_art_rgb",
        ),
        native_scale=2,
        scales=(1, 2, 4, 8, 16, 32),
        download_urls={
            "darwin": {ANY_ARCH: f"{_WAIFU2X_RELEASE}/waifu2x-ncnn-vulkan-20250915-macos.zip"},
            "linux": {
                "x86_64": f"{_WAIFU2X_SELF_RELEASE}/waifu2x-ncnn-vulkan-20250915-linux-x86_64.zip",
                "aarch64": f"{_WAIFU2X_SELF_RELEASE}/waifu2x-ncnn-vulkan-20250915-linux-arm64.zip",
            },
            "win32": {ANY_ARCH: f"{_WAIFU2X_RELEASE}/waifu2x-ncnn-vulkan-20250915-windows.zip"},
        },
    ),
}


def get_tool_meta(tool: SrTool) -> ToolMeta:
    return _TOOL_META[tool]


def get_download_url(tool: SrTool, platform: str, machine: str) -> str:
    """按平台与架构取下载 URL; 该组合无可用包时抛 RuntimeError."""
    by_arch = get_tool_meta(tool).download_urls.get(platform) or {}
    url = by_arch.get(normalize_arch(machine)) or by_arch.get(ANY_ARCH)
    if url is None:
        raise RuntimeError(f"不支持的平台与架构: {platform} {machine}")
    return url
