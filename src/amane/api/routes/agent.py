from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ...agent import AgentService
from ...agent.sql import as_id_subquery_sql
from ...db.models import AgentSession, SavedQueryEntity
from ...db.repository import Repository
from ...utils.model import to_resp
from ..deps import AgentDep, RepoDep, RuntimeDep
from ..models.agent import (
    AgentSessionCreateRequest,
    AgentSessionListResponse,
    AgentSessionResponse,
    AgentSessionTitleRequest,
    AgentSessionTitleResponse,
    AgentSessionUpdateRequest,
    AgentTraceResponse,
    SavedQueryListResponse,
    SavedQueryResponse,
    SavedQueryResultResponse,
    SavedQueryUpdateRequest,
)

router = APIRouter(tags=["agent"])


def _session_response(service: AgentService, session: AgentSession) -> AgentSessionResponse:
    assert session.id is not None
    return AgentSessionResponse(
        id=session.id,
        title=session.title,
        status=session.status,
        thinking=service.session_thinking(session.id),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post("/agent/sessions", status_code=201)
async def create_agent_session(service: AgentDep, req: AgentSessionCreateRequest | None = None) -> AgentSessionResponse:
    title = req.title if req is not None else "新会话"
    session = await service.create_session(title=title)
    return _session_response(service, session)


@router.get("/agent/sessions")
async def list_agent_sessions(service: AgentDep, repo: RepoDep) -> AgentSessionListResponse:
    items = await repo.list_agent_sessions()
    return AgentSessionListResponse(items=[_session_response(service, s) for s in items if s.id is not None])


@router.patch("/agent/sessions/{session_id}")
async def update_agent_session(
    session_id: int, req: AgentSessionUpdateRequest, service: AgentDep, repo: RepoDep
) -> AgentSessionResponse:
    fields = req.model_fields_set
    if not fields:
        raise HTTPException(422, detail="无更新字段")
    title = req.title if "title" in fields else None
    session = await repo.get_agent_session(session_id)
    if session is None:
        raise HTTPException(404, detail="会话不存在")
    if title is not None:
        session = await repo.update_agent_session(session_id, title=title)
        if session is None:
            raise HTTPException(404, detail="会话不存在")
        store = service.store_for(session_id)
        meta = store.read_meta()
        meta["title"] = title
        store.write_meta(meta)
    if "thinking" in fields:
        service.set_session_thinking(session_id, req.thinking)
    return _session_response(service, session)


@router.post("/agent/sessions/{session_id}/title")
async def generate_agent_session_title(
    session_id: int, req: AgentSessionTitleRequest, service: AgentDep, repo: RepoDep
) -> AgentSessionTitleResponse:
    """按首条输入生成标题. 与主回合并行执行, 前端不等回合结束即可刷新列表."""
    if await repo.get_agent_session(session_id) is None:
        raise HTTPException(404, detail="会话不存在")
    return AgentSessionTitleResponse(title=await service.name_session(session_id, req.prompt))


@router.delete("/agent/sessions/{session_id}", status_code=204)
async def delete_agent_session(session_id: int, service: AgentDep) -> None:
    ok = await service.delete_session(session_id)
    if not ok:
        raise HTTPException(404, detail="会话不存在")


@router.get("/agent/sessions/{session_id}/trace")
async def get_agent_trace(session_id: int, service: AgentDep, repo: RepoDep) -> AgentTraceResponse:
    session = await repo.get_agent_session(session_id)
    if session is None:
        raise HTTPException(404, detail="会话不存在")
    store = service.store_for(session_id)
    return AgentTraceResponse(
        meta=store.read_meta(),
        events=store.ui_events(),
        turn_running=service.is_turn_running(session_id),
        last_seq=store.last_seq,
    )


@router.get("/saved-queries")
async def list_saved_queries(
    repo: RepoDep,
    session_id: Annotated[int | None, Query(description="仅该会话下的预设")] = None,
    persisted_only: Annotated[bool, Query(description="仅已保留 (persisted) 的预设")] = False,
) -> SavedQueryListResponse:
    items = await repo.list_saved_queries(session_id=session_id, persisted_only=persisted_only)
    return SavedQueryListResponse(items=[to_resp(SavedQueryResponse, q) for q in items if q.id is not None])


@router.get("/saved-queries/{query_id}")
async def get_saved_query(query_id: int, repo: RepoDep) -> SavedQueryResponse:
    query = await repo.get_saved_query(query_id)
    if query is None:
        raise HTTPException(404, detail="查询预设不存在")
    return to_resp(SavedQueryResponse, query)


@router.patch("/saved-queries/{query_id}")
async def update_saved_query(query_id: int, req: SavedQueryUpdateRequest, repo: RepoDep) -> SavedQueryResponse:
    if req.name is None and req.persisted is None:
        raise HTTPException(422, detail="无更新字段")
    query = await repo.update_saved_query(query_id, name=req.name, persisted=req.persisted)
    if query is None:
        raise HTTPException(404, detail="查询预设不存在")
    return to_resp(SavedQueryResponse, query)


@router.delete("/saved-queries/{query_id}", status_code=204)
async def delete_saved_query(query_id: int, repo: RepoDep, runtime: RuntimeDep) -> None:
    ok = await repo.delete_saved_query(query_id)
    if not ok:
        raise HTTPException(404, detail="查询预设不存在")
    service = runtime.agent_service
    if service is not None:
        service.cache.invalidate(query_id)


@router.get("/saved-queries/{query_id}/result")
async def get_saved_query_result(
    query_id: int,
    service: AgentDep,
    repo: RepoDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 100,
) -> SavedQueryResultResponse:
    query = await repo.get_saved_query(query_id)
    if query is None:
        raise HTTPException(404, detail="查询预设不存在")
    try:
        cached = await service.executor.ensure_cached(query, timeout_ms=service.config.sql_timeout_ms)
    except Exception as exc:
        raise HTTPException(400, detail=f"执行查询失败: {exc}") from exc

    return SavedQueryResultResponse(
        saved_query_id=query_id,
        columns=cached.columns,
        rows=cached.rows[offset : offset + limit],
        offset=offset,
        limit=limit,
        total=len(cached.rows),
    )


async def resolve_saved_query_id_subquery(repo: Repository, saved_query_id: int, expected: SavedQueryEntity) -> str:
    """可嵌入的 ``SELECT id FROM (...)``. 预设 SQL 已过沙箱只读验证; 无需重复校验."""
    query = await repo.get_saved_query(saved_query_id)
    if query is None:
        raise HTTPException(404, detail="查询预设不存在")
    if query.entity != expected:
        raise HTTPException(400, detail=f"查询预设实体为 {query.entity}, 与当前列表不匹配")
    return as_id_subquery_sql(query.sql)
