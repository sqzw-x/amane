"""封面与剧照语义检索的运行时状态; 查询并入 ``/metadata`` (``semantic_query``). 索引走任务系统."""

from fastapi import APIRouter

from ...semantic import SemanticService, model_key
from ..deps import RuntimeDep
from ..models import SemanticModelCounts, SemanticStatusResponse

router = APIRouter(prefix="/semantic", tags=["semantic"])


async def _status_response(service: SemanticService) -> SemanticStatusResponse:
    status = await service.status()
    return SemanticStatusResponse(
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
        return SemanticStatusResponse(enabled=False)
    return await _status_response(service)


@router.post("/release")
async def release_semantic(runtime: RuntimeDep) -> SemanticStatusResponse:
    """释放内存里的向量索引与两个塔; 下次检索重新装载. 内存紧张时手动触发, 不影响库内向量."""
    service = runtime.semantic
    if service is None:
        return SemanticStatusResponse(enabled=False)
    service.release()
    return await _status_response(service)
