"""CLI 参数生成 - 从 SrConfig 生成命令行参数列表.

ncnn-vulkan 实测:
- input 支持文件/目录, 不递归, 只处理 jpg/png/webp.
- output 目录必须预先存在.
- 不支持的格式 decode 失败后跳过, 不中断批处理.
- 模型路径相对于二进制所在目录解析, 与 cwd 无关.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from .tool import SrTool, get_preset_meta

if TYPE_CHECKING:
    from ..config import SrConfig


def build_args(input: Path, output: Path, config: SrConfig, *, gpu_id: int | None = None) -> list[str]:
    """将 SrConfig + input/output 转换为 CLI 参数列表.

    gpu_id: 传给 ``-g``. ``-1`` = ncnn CPU (process_cpu). None = 不传, 上游 auto GPU.
    """
    preset_meta = get_preset_meta(config.preset)
    tool = preset_meta.tool

    cli = ["-i", str(input.absolute()), "-o", str(output.absolute())]

    # 模型: realesrgan 用 -n, waifu2x 用 -m
    if tool == SrTool.REALESRGAN:
        cli += ["-n", preset_meta.model]
    else:
        cli += ["-m", preset_meta.model]

    cli += ["-s", str(preset_meta.scale)]
    cli += ["-f", config.output_format]

    # 降噪 (仅 waifu2x)
    if tool == SrTool.WAIFU2X and preset_meta.noise_level >= 0:
        cli += ["-n", str(preset_meta.noise_level)]

    if config.tta:
        cli.append("-x")

    if gpu_id is not None:
        cli += ["-g", str(gpu_id)]

    return cli
