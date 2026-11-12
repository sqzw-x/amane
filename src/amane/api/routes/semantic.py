"""封面与剧照语义检索的运行时端点: 页面预检、手册诊断与内存释放; 查询并入 ``/metadata``."""

from fastapi import APIRouter

from ...semantic import SemanticService, SemanticStatus, model_key
from ..deps import RuntimeDep
from ..models import SemanticDiagnosticsResponse, SemanticModelCounts, SemanticStatusResponse

router = APIRouter(prefix="/semantic", tags=["semantic"])


def _status_response(status: SemanticStatus) -> SemanticStatusResponse:
    return SemanticStatusResponse(
        enabled=status.enabled,
        available=status.available,
        search_ready=status.search_ready,
        has_embeddings=status.has_embeddings,
        download_size=status.download_size,
    )


async def _diagnostics_response(service: SemanticService) -> SemanticDiagnosticsResponse:
    status = await service.diagnostics()
    return SemanticDiagnosticsResponse(
        enabled=status.enabled,
        backend=status.backend,
        current_model=status.model,
        current_model_label=status.model_label,
        backend_label=status.backend_label,
        text_loaded=status.text_loaded,
        vision_loaded=status.vision_loaded,
        search_ready=status.search_ready,
        index_ready=status.index_ready,
        download_size=status.download_size,
        vision_size=status.vision_size,
        text_size=status.text_size,
        model_path=status.model_path,
        model_bytes=status.model_bytes,
        embeddings_path=status.embeddings_path,
        embeddings_bytes=status.embeddings_bytes,
        indexed_metadata=status.indexed_metadata,
        indexed_at=status.indexed_at,
        models={
            model_key(stats.ref.name, stats.ref.version): SemanticModelCounts(
                count=stats.count, covers=stats.covers, stills=stats.stills
            )
            for stats in status.models
        },
    )


@router.get("/status")
async def semantic_status(runtime: RuntimeDep) -> SemanticStatusResponse:
    service = runtime.semantic
    if service is None:
        return SemanticStatusResponse()
    return _status_response(await service.status())


@router.get("/diagnostics")
async def semantic_diagnostics(runtime: RuntimeDep) -> SemanticDiagnosticsResponse:
    service = runtime.semantic
    if service is None:
        return SemanticDiagnosticsResponse(enabled=False)
    return await _diagnostics_response(service)


@router.post("/release")
async def release_semantic(runtime: RuntimeDep) -> SemanticStatusResponse:
    """释放内存里的向量索引与两个塔; 下次检索重新装载. 内存紧张时手动触发, 不影响库内向量."""
    service = runtime.semantic
    if service is None:
        return SemanticStatusResponse()
    service.release()
    return _status_response(await service.status())
