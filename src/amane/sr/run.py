import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .args import build_args
from .binary import ensure_binary, get_bundled_binary_path
from .device import has_vulkan_icd
from .tool import SrTool, get_preset_meta

if TYPE_CHECKING:
    from ..config import SrConfig

logger = structlog.get_logger()


@dataclass
class SrResult:
    """超分执行结果."""

    success: bool
    output: Path | None = None
    error: str | None = None
    duration_ms: float = 0
    input_size: int = 0
    output_size: int = 0
    stdout: str = ""
    stderr: str = ""


async def run_SR(input: Path, output: Path, config: SrConfig, data_dir: Path, *, timeout: float = 600) -> SrResult:
    """执行图像超分 - 检查二进制, 生成参数, 运行进程.

    Docker 镜像捆绑的是 patched waifu2x: 有 ICD 走 Vulkan GPU, 无 ICD 传 ``-g -1``
    用 ncnn ``process_cpu``. 未捆绑时 (桌面) 仍按需下上游 zip.

    Args:
        input: 输入图片路径 (文件或目录).
        output: 输出路径.
        config: SrConfig 配置.
        data_dir: 应用数据目录.
        timeout: 进程超时秒数, 默认 600s.

    Returns:
        SrResult (不抛异常).
    """
    started = time.monotonic()
    pm = get_preset_meta(config.preset)
    tool = pm.tool
    log = logger.bind(preset=config.preset, tool=tool, input=str(input), output=str(output))

    input_size = input.stat().st_size if input.is_file() else 0

    try:
        gpu_id: int | None = None
        bundled = get_bundled_binary_path(tool)
        if bundled is not None:
            # 同一份 patched 二进制: 有 ICD 不传 -g (auto GPU), 没有则 -g -1 (process_cpu).
            binary_path = bundled
            if not has_vulkan_icd():
                gpu_id = -1
        elif not has_vulkan_icd() and tool == SrTool.REALESRGAN:
            duration = (time.monotonic() - started) * 1000
            error = "realesrgan 需要 Vulkan GPU; 无 GPU 时请改用 waifu-photo-2x"
            log.error("sr backend unavailable", error=error)
            return SrResult(False, error=error, duration_ms=duration)
        else:
            binary_path = await ensure_binary(tool, data_dir)
        log.debug("sr binary ready", path=str(binary_path), gpu_id=gpu_id)

        args = build_args(input, output, config, gpu_id=gpu_id)
        log.debug("sr args", args=args)

        if input.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
        else:
            output.mkdir(parents=True, exist_ok=True)

        log.info("sr process starting", backend="cpu" if gpu_id == -1 else "vulkan")
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=binary_path.parent,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            duration = (time.monotonic() - started) * 1000
            log.error("sr process timeout", timeout=timeout)
            return SrResult(False, error=f"进程超时 ({timeout=}s)", duration_ms=duration)

        duration = (time.monotonic() - started) * 1000
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        if proc.returncode != 0:
            log.error("sr process failed", returncode=proc.returncode, stderr=stderr)
            return SrResult(
                False,
                error=f"非零返回值 {proc.returncode}: {stderr[:200]}",
                duration_ms=duration,
                stdout=stdout,
                stderr=stderr,
            )

        output_size = output.stat().st_size if output.is_file() else 0
        if output.is_file() or (output.is_dir() and any(output.iterdir())):
            if input_size and output_size:
                log.info(
                    "sr completed",
                    input_size=_fmt_size(input_size),
                    output_size=_fmt_size(output_size),
                    ratio=f"{output_size / input_size:.1f}x",
                    duration_ms=round(duration),
                )
            else:
                log.info("sr completed", duration_ms=round(duration))
            return SrResult(
                True, output, duration_ms=duration, input_size=input_size, output_size=output_size, stdout=stdout
            )
        return SrResult(False, error="进程正常退出但无输出文件", duration_ms=duration, stdout=stdout, stderr=stderr)

    except ValueError as e:
        duration = (time.monotonic() - started) * 1000
        log.error("sr config error", error=str(e))
        return SrResult(False, error=str(e), duration_ms=duration)

    except FileNotFoundError as e:
        duration = (time.monotonic() - started) * 1000
        log.error("sr binary not found", error=str(e))
        return SrResult(False, error=f"二进制不存在: {e}", duration_ms=duration)

    except OSError as e:
        duration = (time.monotonic() - started) * 1000
        log.error("sr io error", error=str(e))
        return SrResult(False, error=str(e), duration_ms=duration)

    except Exception as e:
        duration = (time.monotonic() - started) * 1000
        log.error("sr unexpected error", error=str(e), exc_info=True)
        return SrResult(False, error=str(e), duration_ms=duration)


def _fmt_size(n: int) -> str:
    """人类可读的文件大小."""
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}GB"
