"""语义检索任务: 索引对账与性能测试, 分别见 ``SemanticService.build_index`` / ``benchmark``.

模型文件只在索引任务里按需下载; 单张图失败只计数, 不阻断整批.
"""

from ..semantic import SemanticService
from .models import (
    SemanticBenchmarkPayload,
    SemanticBenchmarkResult,
    SemanticIndexPayload,
    SemanticIndexResult,
)
from .protocol import TaskHandler, TaskResult


class SemanticIndexHandler(TaskHandler[SemanticIndexPayload, SemanticIndexResult]):
    def __init__(self, service: SemanticService) -> None:
        super().__init__(SemanticIndexPayload, SemanticIndexResult)
        self._service = service

    async def handle(self, payload: SemanticIndexPayload) -> TaskResult[SemanticIndexResult]:
        stats = await self._service.build_index(
            limit=payload.limit,
            force=payload.force,
            progress=self.report_progress,
            kinds=payload.kinds,
        )
        return TaskResult(
            success=True,
            result=SemanticIndexResult(
                scanned=stats.scanned,
                indexed=stats.indexed,
                skipped=stats.skipped,
                missing=stats.missing,
                failed=stats.failed,
            ),
        )


class SemanticBenchmarkHandler(TaskHandler[SemanticBenchmarkPayload, SemanticBenchmarkResult]):
    def __init__(self, service: SemanticService) -> None:
        super().__init__(SemanticBenchmarkPayload, SemanticBenchmarkResult)
        self._service = service

    async def handle(self, payload: SemanticBenchmarkPayload) -> TaskResult[SemanticBenchmarkResult]:
        stats = await self._service.benchmark()
        return TaskResult(
            success=True,
            result=SemanticBenchmarkResult(
                backend=stats.backend,
                model=stats.model,
                images_per_second=stats.images_per_second,
                query_ms=stats.query_ms,
                load_ms=stats.load_ms,
                samples=stats.samples,
                measured_at=stats.measured_at,
            ),
        )
