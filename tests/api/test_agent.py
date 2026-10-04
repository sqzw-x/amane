"""Agent session API 表测试 (不调用真实 LLM)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx2 import AsyncClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from amane.agent.naming import fallback_title
from amane.agent.rows import ApprovalsRow, CancelledRow
from amane.agent.service import AgentService
from amane.db.models import SavedQueryEntity
from amane.db.repository import Repository


@pytest.mark.asyncio
async def test_delete_session_invalidates_ephemeral_cache(app: FastAPI, client: AsyncClient, repo: Repository) -> None:
    """删会话清理未保留预设时必须失效结果缓存 (预设 id 是 rowid, 可复用)."""
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)
    session = await service.create_session(title="cache")
    assert session.id is not None
    sq = await repo.create_saved_query(
        name="tmp",
        sql="SELECT 1 AS n",
        entity=SavedQueryEntity.DATA,
        session_id=session.id,
        persisted=False,
    )
    assert sq.id is not None

    r = await client.get(f"/saved-queries/{sq.id}/result")
    assert r.status_code == 200
    assert service.cache.get(sq.id, "SELECT 1 AS n") is not None

    r = await client.delete(f"/agent/sessions/{session.id}")
    assert r.status_code == 204
    assert service.cache.get(sq.id, "SELECT 1 AS n") is None
    assert await repo.get_saved_query(sq.id) is None


@pytest.mark.asyncio
async def test_delete_session_keeps_persisted_query(client: AsyncClient, repo: Repository) -> None:
    session = await repo.create_agent_session()
    assert session.id is not None
    sq = await repo.create_saved_query(
        name="keep",
        sql="SELECT id FROM metadata WHERE 0",
        entity=SavedQueryEntity.METADATA,
        session_id=session.id,
        persisted=False,
    )
    assert sq.id is not None
    assert await repo.persist_saved_queries([sq.id]) == (1, 0)

    r = await client.delete(f"/agent/sessions/{session.id}")
    assert r.status_code == 204

    kept = await repo.get_saved_query(sq.id)
    assert kept is not None
    assert kept.persisted is True
    assert kept.session_id is None


@pytest.mark.asyncio
async def test_delete_session_with_fk_and_ephemeral(repo: Repository) -> None:
    """生产库 PRAGMA foreign_keys=ON; 无 Relationship 时须先 flush 子表再删会话."""
    from sqlalchemy import text

    async with repo._engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=ON"))

    session = await repo.create_agent_session()
    assert session.id is not None
    ephemeral = await repo.create_saved_query(
        name="tmp",
        sql="SELECT id FROM metadata WHERE 0",
        entity=SavedQueryEntity.METADATA,
        session_id=session.id,
        persisted=False,
    )
    persisted = await repo.create_saved_query(
        name="keep",
        sql="SELECT id FROM metadata WHERE 0",
        entity=SavedQueryEntity.METADATA,
        session_id=session.id,
        persisted=True,
    )
    assert ephemeral.id is not None and persisted.id is not None

    assert await repo.delete_agent_session(session.id) is True
    assert await repo.get_agent_session(session.id) is None
    assert await repo.get_saved_query(ephemeral.id) is None
    kept = await repo.get_saved_query(persisted.id)
    assert kept is not None
    assert kept.session_id is None


@pytest.mark.asyncio
async def test_generate_session_title_from_model(client: AsyncClient, app: FastAPI) -> None:
    """模型可用时取正文作标题, 并写回会话索引与 meta."""
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)

    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("「影片清单」")])

    service.naming_model = FunctionModel(respond)

    r = await client.post("/agent/sessions", json={"title": "新会话"})
    session_id = r.json()["id"]

    r = await client.post(f"/agent/sessions/{session_id}/title", json={"prompt": "列出全部影片"})
    assert r.status_code == 200
    assert r.json() == {"title": "影片清单"}

    r = await client.get("/agent/sessions")
    assert next(i["title"] for i in r.json()["items"] if i["id"] == session_id) == "影片清单"
    r = await client.get(f"/agent/sessions/{session_id}/trace")
    assert r.json()["meta"]["title"] == "影片清单"


@pytest.mark.asyncio
async def test_generate_session_title_falls_back_without_model(client: AsyncClient) -> None:
    """未配置模型 (测试环境无 api_key) 时回退首条输入截断."""
    prompt = "find every movie released after 2020 in the library"
    r = await client.post("/agent/sessions", json={"title": "新会话"})
    session_id = r.json()["id"]

    r = await client.post(f"/agent/sessions/{session_id}/title", json={"prompt": prompt})
    assert r.status_code == 200
    assert r.json() == {"title": fallback_title(prompt)}

    r = await client.get("/agent/sessions")
    assert next(i["title"] for i in r.json()["items"] if i["id"] == session_id) == fallback_title(prompt)


@pytest.mark.asyncio
async def test_generate_session_title_rejects_bad_request(client: AsyncClient) -> None:
    r = await client.post("/agent/sessions/999999/title", json={"prompt": "列出全部影片"})
    assert r.status_code == 404
    r = await client.post("/agent/sessions/1/title", json={"prompt": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_cancel_idle_and_missing(client: AsyncClient, repo: Repository) -> None:
    session = await repo.create_agent_session()
    assert session.id is not None
    r = await client.post(f"/agent/sessions/{session.id}/agui/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": False}

    r = await client.post("/agent/sessions/999999/agui/cancel")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_running_turn(app: FastAPI, client: AsyncClient) -> None:
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)
    session = await service.create_session(title="c")
    assert session.id is not None
    sid = session.id
    store = service.store_for(sid)
    gate = asyncio.Event()

    async def fake_turn() -> None:
        store.set_turn_running(True)
        try:
            await gate.wait()
        except asyncio.CancelledError:
            await store.append_row(CancelledRow(type="cancelled"))
            raise
        finally:
            store.set_turn_running(False)

    service.track_turn(sid, asyncio.create_task(fake_turn(), name=f"agent-turn-{sid}"))
    await asyncio.sleep(0)
    assert service.is_turn_running(sid)

    r = await client.post(f"/agent/sessions/{sid}/agui/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}
    assert not service.is_turn_running(sid)
    assert any(isinstance(row, CancelledRow) for row in store.read_events())


@pytest.mark.asyncio
async def test_cancel_writes_terminal_rows_without_task(app: FastAPI, client: AsyncClient) -> None:
    """无后台任务但标记为进行中 (进程异常态) 时取消: 补写终态行与空审批快照并复位."""
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)
    session = await service.create_session(title="orphan-turn")
    assert session.id is not None
    store = service.store_for(session.id)
    store.set_turn_running(True)

    r = await client.post(f"/agent/sessions/{session.id}/agui/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}

    assert not store.turn_running
    rows = store.read_events()
    assert any(isinstance(row, CancelledRow) for row in rows)
    assert [row.interrupts for row in rows if isinstance(row, ApprovalsRow)] == [[]]
