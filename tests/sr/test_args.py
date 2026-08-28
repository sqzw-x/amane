"""build_args 测试 - preset 驱动参数生成."""

from pathlib import Path

from amane.config import SrConfig
from amane.sr import SrPreset, build_args


def _cfg(**kw) -> SrConfig:
    """快捷构造 SrConfig. preset 默认 realesr-photo-4x."""
    defaults = {"enabled": True, "preset": SrPreset.REALESR_PHOTO_4X}
    return SrConfig(**(defaults | kw))


class TestBuildArgsRealesrgan:
    def test_minimal(self):
        result = build_args(Path("in.jpg"), Path("out.jpg"), _cfg())
        assert result == [
            "-i",
            str(Path("in.jpg").absolute()),
            "-o",
            str(Path("out.jpg").absolute()),
            "-n",
            "realesrgan-x4plus",
            "-s",
            "4",
            "-f",
            "jpg",
        ]

    def test_with_tta_custom_format(self):
        inp = Path("in.png")
        out = Path("out.png")
        result = build_args(inp, out, _cfg(output_format="png", tta=True))
        assert result == [
            "-i",
            str(inp.absolute()),
            "-o",
            str(out.absolute()),
            "-n",
            "realesrgan-x4plus",
            "-s",
            "4",
            "-f",
            "png",
            "-x",
        ]

    def test_noise_ignored_for_realesrgan(self):
        """Real-ESRGAN 预设不含降噪, -n 仅用于模型名."""
        result = build_args(Path("a.jpg"), Path("b.jpg"), _cfg())
        # 只有一个 -n (模型), 没有降噪 -n
        assert result.count("-n") == 1


class TestBuildArgsWaifu2x:
    def test_minimal(self):
        inp = Path("in.jpg")
        out = Path("out.jpg")
        result = build_args(inp, out, _cfg(preset=SrPreset.WAIFU_PHOTO_2X))
        assert result == [
            "-i",
            str(inp.absolute()),
            "-o",
            str(out.absolute()),
            "-m",
            "models-upconv_7_photo",
            "-s",
            "2",
            "-f",
            "jpg",
        ]

    def test_no_noise_flag(self):
        """waifu2x 预设 noise_level=-1, 不输出 -n 降噪参数."""
        result = build_args(Path("a.jpg"), Path("b.jpg"), _cfg(preset=SrPreset.WAIFU_PHOTO_2X))
        assert "-n" not in result

    def test_gpu_id_cpu(self):
        result = build_args(Path("a.jpg"), Path("b.jpg"), _cfg(preset=SrPreset.WAIFU_PHOTO_2X), gpu_id=-1)
        assert result[-2:] == ["-g", "-1"]


class TestPresetMeta:
    def test_realesr_preset_resolves_correctly(self):
        cfg = _cfg(preset=SrPreset.REALESR_PHOTO_4X)
        assert cfg.preset == SrPreset.REALESR_PHOTO_4X

    def test_waifu_preset_resolves_correctly(self):
        cfg = _cfg(preset=SrPreset.WAIFU_PHOTO_2X)
        assert cfg.preset == SrPreset.WAIFU_PHOTO_2X

    def test_preset_is_required_field(self):
        """preset 有默认值, 不传也能构造."""
        cfg = SrConfig()
        assert cfg.preset == SrPreset.WAIFU_PHOTO_2X
