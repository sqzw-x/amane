"""就地超分低质图. 跳过判据是 ``meta`` 的 sr 标记且磁盘仍是记录的那份字节 (覆盖原文件, URL 不变).
单个失败不阻断整批.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..media import UpscaleOutcome, needs_upscale, probe_size
from ..media.pipeline import sr_args_dict
from ..sr import run_SR
from .models import UpscalePayload, UpscaleResult
from .protocol import TaskHandler, TaskResult

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..media import ResourceStore

logger = structlog.get_logger()

_IMAGE_MIME_PREFIX = "image/"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _is_image(mime: str | None, file_path: str) -> bool:
    if mime:
        return mime.startswith(_IMAGE_MIME_PREFIX)
    return Path(file_path).suffix.lower() in _IMAGE_EXTS


class UpscaleHandler(TaskHandler[UpscalePayload, UpscaleResult]):
    def __init__(self, resource_store: ResourceStore, config: HotSettings):
        super().__init__(payload_t=UpscalePayload, result_t=UpscaleResult)
        self._store = resource_store
        self._config = config

    async def _sr_producer(self, src: Path, out: Path) -> bool:
        result = await run_SR(src, out, self._config.sr, self._store.data_dir)
        return result.success

    async def handle(self, payload: UpscalePayload) -> TaskResult[UpscaleResult]:
        max_dim = payload.max_dim_threshold or self._config.sr.max_dim_threshold
        max_bytes = payload.max_bytes_threshold or self._config.sr.max_bytes_threshold

        resources = await self._store.list_all()
        scanned = upscaled = skipped = failed = 0

        for res in resources:
            if upscaled >= payload.limit:
                break
            scanned += 1

            if not _is_image(res.mime_type, res.file_path):
                skipped += 1
                continue

            full = self._store.full_path(res)
            size = probe_size(full)
            file_bytes = full.stat().st_size if full.exists() else 0
            # 未达阈值则跳过. 已超分的图尺寸已达阈值, 同样在此跳过: 跳过判据不看 sr 标记.
            if not needs_upscale(size, file_bytes, max_dim_threshold=max_dim, max_bytes_threshold=max_bytes):
                skipped += 1
                continue

            # 存量标记与盘上字节不一致的行照常进入超分, 由锁内判据决定重跑或跳过.
            outcome = await self._store.upscale_in_place(res, sr_args_dict(self._config.sr), self._sr_producer)
            if outcome is UpscaleOutcome.UPSCALED:
                upscaled += 1
            elif outcome is UpscaleOutcome.FAILED:
                failed += 1
            else:
                skipped += 1

        logger.info("upscale completed", scanned=scanned, upscaled=upscaled, skipped=skipped, failed=failed)
        return TaskResult(
            success=failed == 0,
            result=UpscaleResult(scanned=scanned, upscaled=upscaled, skipped=skipped, failed=failed),
        )
