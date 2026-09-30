from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ...agent import AgentService
from ...agent.sql import SqlSandboxError, as_id_subquery_sql
from ...db.models import DEFAULT_SESSION_TITLE, AgentSession, SavedQueryEntity
from ...db.repo_types import SavedQueryUpdates
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
    SavedQueryBatchDeleteResponse,
    SavedQueryBatchIdsRequest,
    SavedQueryBatchPersistResponse,
    SavedQueryCreateRequest,
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
    title = req.title if req is not None else DEFAULT_SESSION_TITLE
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


async def _validate_saved_query(service: AgentService, sql: str, entity: SavedQueryEntity) -> None:
    """创建 / 编辑前在只读沙箱里试跑; 截断结果不入缓存."""
    try:
        await service.executor.validate_saved_query(sql, entity=entity, timeout_ms=service.config.sql_timeout_ms)
    except ValueError as exc:
        raise HTTPException(400, detail=f"{entity.value} 预设的 SQL 必须返回 id 列") from exc
    except SqlSandboxError as exc:
        raise HTTPException(400, detail=f"SQL 校验失败: {exc}") from exc


@router.post("/saved-queries", status_code=201)
async def create_saved_query(req: SavedQueryCreateRequest, repo: RepoDep, service: AgentDep) -> SavedQueryResponse:
    """手动创建: 无会话归属, 直接已保留."""
    await _validate_saved_query(service, req.sql, req.entity)
    query = await repo.create_saved_query(
        name=req.name,
        sql=req.sql,
        entity=req.entity,
        description=req.description,
        persisted=True,
    )
    return to_resp(SavedQueryResponse, query)


@router.patch("/saved-queries/{query_id}")
async def update_saved_query(
    query_id: int, req: SavedQueryUpdateRequest, repo: RepoDep, service: AgentDep
) -> SavedQueryResponse:
    """仅内容字段; 类型 / 归属 / 保留态不可改. SQL 变化才重校验并失效缓存."""
    if not req.model_fields_set:
        raise HTTPException(422, detail="无更新字段")
    query = await repo.get_saved_query(query_id)
    if query is None:
        raise HTTPException(404, detail="查询预设不存在")

    updates: SavedQueryUpdates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.description is not None:
        updates["description"] = req.description
    sql_changed = False
    if req.sql is not None:
        # 请求模型已 strip; 与库中 SQL 比较, 变化才重校验并失效缓存.
        sql_changed = req.sql != query.sql.strip()
        if sql_changed:
            await _validate_saved_query(service, req.sql, query.entity)
            updates["sql"] = req.sql

    if not updates:
        return to_resp(SavedQueryResponse, query)
    updated = await repo.update_saved_query(query_id, **updates)
    if updated is None:  # pragma: no cover - 上文已确认存在
        raise HTTPException(404, detail="查询预设不存在")
    if sql_changed:
        service.cache.invalidate(query_id)
    return to_resp(SavedQueryResponse, updated)


@router.post("/saved-queries/batch/delete")
async def batch_delete_saved_queries(
    req: SavedQueryBatchIdsRequest, repo: RepoDep, runtime: RuntimeDep
) -> SavedQueryBatchDeleteResponse:
    """重复 id 只处理一次; 缓存失效尽力而为, 不因 AgentService 缺失而失败."""
    deleted, missing = await repo.delete_saved_queries(req.ids)
    service = runtime.agent_service
    if service is not None:
        # 缓存键即预设 id (rowid 可复用), 缺失键的 invalidate 是 no-op.
        for query_id in req.ids:
            service.cache.invalidate(query_id)
    return SavedQueryBatchDeleteResponse(deleted=deleted, missing=missing)


@router.post("/saved-queries/batch/persist")
async def batch_persist_saved_queries(req: SavedQueryBatchIdsRequest, repo: RepoDep) -> SavedQueryBatchPersistResponse:
    """置为已保留并解绑会话; 幂等."""
    persisted, missing = await repo.persist_saved_queries(req.ids)
    return SavedQueryBatchPersistResponse(persisted=persisted, missing=missing)


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
