"""读取视频的画面尺寸, 供分辨率相位使用; 只读取容器与流参数, 不解码画面."""

from __future__ import annotations

from typing import TYPE_CHECKING

import av
import structlog
from av.error import FFmpegError

from ..parsing import VideoSize
from ..utils.threads import in_thread

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

# 网盘挂载点上读取可能持续变慢; 超时由 FFmpeg 在两次读取之间检查, 无法中断单次阻塞的系统调用.
_PROBE_TIMEOUT_S = 30.0


@in_thread
def probe_video_size(path: Path) -> VideoSize | None:
    """首个视频流的编码尺寸.

    无法打开、没有视频流或尺寸为零时返回 ``None`` 而非抛异常 — .strm 占位文件与损坏文件本就读不出尺寸,
    调用方据此回退文件名检测.
    """
    try:
        with av.open(str(path), timeout=_PROBE_TIMEOUT_S) as container:
            streams = container.streams.video
            if not streams:
                logger.debug("video probe skipped: no video stream", path=str(path))
                return None
            width, height = streams[0].width, streams[0].height
    except (FFmpegError, OSError) as e:
        logger.debug("video probe failed", path=str(path), error=str(e))
        return None
    if width <= 0 or height <= 0:
        logger.debug("video probe skipped: empty size", path=str(path), width=width, height=height)
        return None
    return VideoSize(width, height)
