"""saved-queries API 表测试."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx2 import AsyncClient

from amane.agent.service import AgentService
from amane.db.models import SavedQueryEntity
from amane.db.repository import Repository


@pytest.mark.asyncio
async def test_saved_query_crud_and_metadata_filter(client: AsyncClient, repo: Repository) -> None:
    """Agent 交付路径的预设: 改名 / 保留 / 筛选深链 / 结果 / 批量删除."""
    session = await repo.create_agent_session(title="t")
    assert session.id is not None

    m1 = await repo.upsert_metadata(number="ABC-001", title="First")
    m2 = await repo.upsert_metadata(number="ABC-002", title="Second")
    assert m1.id is not None and m2.id is not None

    sq = await repo.create_saved_query(
        name="两部片子",
        sql=f"SELECT id FROM metadata WHERE id IN ({m1.id}, {m2.id})",
        entity=SavedQueryEntity.METADATA,
        session_id=session.id,
    )
    assert sq.id is not None

    r = await client.get(f"/saved-queries/{sq.id}")
    assert r.status_code == 200
    assert r.json()["name"] == "两部片子"
    assert r.json()["description"] == ""
    assert r.json()["persisted"] is False

    r = await client.patch(f"/saved-queries/{sq.id}", json={"name": "改名"})
    assert r.status_code == 200
    assert r.json()["name"] == "改名"

    # 保留: 批量端点置 persisted 并解绑会话; 已保留行重复调用幂等
    r = await client.post("/saved-queries/batch", json={"action": "persist", "ids": [sq.id, 999999]})
    assert r.status_code == 200
    assert r.json() == {"affected": 1, "missing": 1}
    r = await client.get(f"/saved-queries/{sq.id}")
    assert r.json()["persisted"] is True
    assert r.json()["session_id"] is None

    r = await client.get("/metadata", params={"saved_query_id": sq.id})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert {i["id"] for i in data["items"]} == {m1.id, m2.id}

    # AND 其它筛选: 标题关键字收窄到一部
    r = await client.get("/metadata", params={"saved_query_id": sq.id, "search": "First"})
    assert r.status_code == 200
    narrowed = r.json()
    assert narrowed["total"] == 1
    assert narrowed["items"][0]["id"] == m1.id

    r = await client.get(f"/saved-queries/{sq.id}/result", params={"limit": 10})
    assert r.status_code == 200
    result = r.json()
    assert result["total"] == 2
    assert len(result["rows"]) == 2
    assert "entity_ids" not in result

    r = await client.get("/saved-queries", params={"persisted_only": True})
    assert r.status_code == 200
    assert any(i["id"] == sq.id for i in r.json()["items"])

    r = await client.post("/saved-queries/batch", json={"action": "delete", "ids": [sq.id]})
    assert r.status_code == 200
    assert r.json() == {"affected": 1, "missing": 0}
    r = await client.get(f"/saved-queries/{sq.id}")
    assert r.status_code == 404


_CREATE_VALID_CASES = [
    ("metadata", "SELECT id FROM metadata"),
    ("actor", "SELECT id FROM actors"),
    ("data", "SELECT 1 AS n"),
]


@pytest.mark.asyncio
async def test_create_saved_query_manual(client: AsyncClient) -> None:
    """手动创建: strip 落库, 无会话归属且直接已保留; 请求外键被忽略."""
    for entity, sql in _CREATE_VALID_CASES:
        r = await client.post(
            "/saved-queries",
            json={
                "name": "  手动预设  ",
                "description": "  说明  ",
                "sql": f"  {sql}  ",
                "entity": entity,
                "persisted": False,
                "session_id": 999,
            },
        )
        assert r.status_code == 201, entity
        body = r.json()
        assert body["name"] == "手动预设"
        assert body["description"] == "说明"
        assert body["sql"] == sql
        assert body["entity"] == entity
        assert body["persisted"] is True
        assert body["session_id"] is None

        r = await client.get(f"/saved-queries/{body['id']}")
        assert r.status_code == 200
        assert r.json() == body
        r = await client.get(f"/saved-queries/{body['id']}/result")
        assert r.status_code == 200


_CREATE_REJECT_CASES = [
    (
        {"name": "  ", "sql": "SELECT id FROM metadata", "entity": "metadata"},
        422,
        "String should have at least 1 character",
    ),
    ({"name": "x", "sql": "  ", "entity": "data"}, 422, "String should have at least 1 character"),
    ({"name": "x", "sql": "SELEC id FROM metadata", "entity": "metadata"}, 400, "SQL 校验失败"),
    ({"name": "x", "sql": "SELECT id FROM metadata; SELECT 1", "entity": "metadata"}, 400, "SQL 校验失败"),
    ({"name": "x", "sql": "DELETE FROM metadata", "entity": "metadata"}, 400, "SQL 校验失败"),
    ({"name": "x", "sql": "SELECT number FROM metadata", "entity": "metadata"}, 400, "id 列"),
    ({"name": "x", "sql": "SELECT name FROM actors", "entity": "actor"}, 400, "id 列"),
    ({"name": "x", "sql": "SELECT 1", "entity": "unknown"}, 422, None),
]


@pytest.mark.asyncio
async def test_create_saved_query_rejects_invalid(client: AsyncClient, repo: Repository) -> None:
    await repo.upsert_metadata(number="ABC-001")
    for body, status, fragment in _CREATE_REJECT_CASES:
        r = await client.post("/saved-queries", json=body)
        assert r.status_code == status, body
        if fragment is not None:
            assert fragment in r.text, body
        assert (await client.get("/saved-queries")).json()["items"] == []


# 名称 / 描述上限; 空白拒绝由 _CREATE_REJECT_CASES 与更新用例覆盖
_CONTENT_OVER_MAX = (("name", "x" * 201), ("description", "y" * 2001))


@pytest.mark.asyncio
async def test_saved_query_content_length_limits(client: AsyncClient, repo: Repository) -> None:
    """创建与更新两条路径的长度上限一致, 越界拒绝, 边界值合法."""
    for field, value in _CONTENT_OVER_MAX:
        body: dict[str, object] = {"name": "合法", "sql": "SELECT 1 AS n", "entity": "data", field: value}
        r = await client.post("/saved-queries", json=body)
        assert r.status_code == 422, field

    sq = await repo.create_saved_query(name="合法", sql="SELECT 1 AS n", entity=SavedQueryEntity.DATA, persisted=True)
    assert sq.id is not None
    for field, value in _CONTENT_OVER_MAX:
        r = await client.patch(f"/saved-queries/{sq.id}", json={field: value})
        assert r.status_code == 422, field

    r = await client.post(
        "/saved-queries",
        json={"name": "x" * 200, "description": "y" * 2000, "sql": "SELECT 1 AS n", "entity": "data"},
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_update_saved_query_content_and_rejection(client: AsyncClient, repo: Repository) -> None:
    m1 = await repo.upsert_metadata(number="ABC-001", title="First")
    m2 = await repo.upsert_metadata(number="ABC-002", title="Second")
    assert m1.id is not None and m2.id is not None
    sq = await repo.create_saved_query(
        name="旧名",
        description="旧描述",
        sql=f"SELECT id FROM metadata WHERE id = {m1.id}",
        entity=SavedQueryEntity.METADATA,
        persisted=True,
    )
    assert sq.id is not None
    path = f"/saved-queries/{sq.id}"

    # 预热缓存: 旧 SQL 一行
    r = await client.get(f"{path}/result")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = await client.patch(
        path,
        json={
            "name": "  新名  ",
            "description": "新描述",
            "sql": f"SELECT id FROM metadata WHERE id IN ({m1.id}, {m2.id})",
            "entity": "data",
            "persisted": False,
            "session_id": None,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "新名"
    assert body["description"] == "新描述"
    assert body["entity"] == "metadata"  # 类型不可改, 多余键被忽略
    assert body["persisted"] is True
    assert body["session_id"] is None

    # SQL 变更失效缓存: 重新执行得到两行
    r = await client.get(f"{path}/result")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = await client.patch(path, json={"description": ""})
    assert r.status_code == 200
    assert r.json()["description"] == ""

    # 无字段 / null / 空白 / 未知 id
    r = await client.patch(path, json={})
    assert r.status_code == 422
    assert "无更新字段" in r.text
    r = await client.patch(path, json={"name": None})
    assert r.status_code == 422
    r = await client.patch(path, json={"description": None})
    assert r.status_code == 422
    r = await client.patch(path, json={"sql": None})
    assert r.status_code == 422
    r = await client.patch(path, json={"name": "   "})
    assert r.status_code == 422
    assert "String should have at least 1 character" in r.text
    r = await client.patch("/saved-queries/999999", json={"name": "x"})
    assert r.status_code == 404

    # 原子性: 非法 SQL 时 name 不落库
    r = await client.patch(path, json={"name": "不应落库", "sql": "SELECT number FROM metadata"})
    assert r.status_code == 400
    assert "id 列" in r.text
    assert (await client.get(path)).json()["name"] == "新名"


@pytest.mark.asyncio
async def test_batch_delete_saved_queries(app: FastAPI, client: AsyncClient, repo: Repository) -> None:
    """批量删除: 不去重, 结果 + missing == len(ids); 缓存同步失效."""
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)

    async def make(name: str) -> int:
        row = await repo.create_saved_query(
            name=name,
            sql="SELECT 1 AS n",
            entity=SavedQueryEntity.DATA,
            persisted=True,
        )
        assert row.id is not None
        return row.id

    a = await make("a")
    b = await make("b")

    # 预热缓存, 删除后必须失效
    r = await client.get(f"/saved-queries/{a}/result")
    assert r.status_code == 200
    assert service.cache.get(a, "SELECT 1 AS n") is not None

    r = await client.post("/saved-queries/batch", json={"action": "delete", "ids": [a, b, 999999]})
    assert r.status_code == 200
    assert r.json() == {"affected": 2, "missing": 1}
    assert service.cache.get(a, "SELECT 1 AS n") is None
    assert (await client.get(f"/saved-queries/{a}/result")).status_code == 404
    assert await repo.get_saved_query(b) is None

    # 重复 id 只处理一次
    c = await make("c")
    r = await client.post("/saved-queries/batch", json={"action": "delete", "ids": [c, c]})
    assert r.json() == {"affected": 1, "missing": 0}

    r = await client.post("/saved-queries/batch", json={"action": "delete", "ids": []})
    assert r.status_code == 422

    # 上限 1000: 越界请求不触发 IN 查询的变量数超限
    r = await client.post("/saved-queries/batch", json={"action": "delete", "ids": list(range(1001))})
    assert r.status_code == 422


_BATCH_INVALID_CASES = [
    {"action": "archive", "ids": [1]},
    {"ids": [1]},
    {"action": "delete"},
]


@pytest.mark.asyncio
async def test_batch_saved_queries_rejects_invalid(client: AsyncClient) -> None:
    for body in _BATCH_INVALID_CASES:
        r = await client.post("/saved-queries/batch", json=body)
        assert r.status_code == 422, body


@pytest.mark.asyncio
async def test_batch_persist_saved_queries(client: AsyncClient, repo: Repository) -> None:
    """批量保留: 幂等; 未保留的会话预设解绑会话."""
    session = await repo.create_agent_session(title="persist")
    assert session.id is not None
    ephemeral = await repo.create_saved_query(
        name="tmp",
        sql="SELECT 1 AS n",
        entity=SavedQueryEntity.DATA,
        session_id=session.id,
        persisted=False,
    )
    assert ephemeral.id is not None

    r = await client.post("/saved-queries/batch", json={"action": "persist", "ids": [ephemeral.id, 999999]})
    assert r.status_code == 200
    assert r.json() == {"affected": 1, "missing": 1}

    row = await repo.get_saved_query(ephemeral.id)
    assert row is not None
    assert row.persisted is True
    assert row.session_id is None

    r = await client.post("/saved-queries/batch", json={"action": "persist", "ids": [ephemeral.id]})
    assert r.json() == {"affected": 1, "missing": 0}


@pytest.mark.asyncio
async def test_data_saved_query_result_and_filter_rejection(client: AsyncClient, repo: Repository) -> None:
    """data 交付: 全列结果可分页; 不可作 /meta /actors 筛选 (400)."""
    session = await repo.create_agent_session(title="data")
    assert session.id is not None
    await repo.upsert_metadata(number="ABC-001", title="First")
    await repo.upsert_metadata(number="ABC-002", title="Second")
    sq = await repo.create_saved_query(
        name="片商排行",
        sql="SELECT number AS studio, 1 AS n FROM metadata ORDER BY number",
        entity=SavedQueryEntity.DATA,
        session_id=session.id,
    )
    assert sq.id is not None

    r = await client.get(f"/saved-queries/{sq.id}/result", params={"offset": 0, "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["studio", "n"]
    assert body["total"] == 2
    assert len(body["rows"]) == 1

    r = await client.get("/metadata", params={"saved_query_id": sq.id})
    assert r.status_code == 400
    r = await client.get("/actors", params={"saved_query_id": sq.id})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_saved_queries_filters(client: AsyncClient, repo: Repository) -> None:
    session = await repo.create_agent_session(title="filters")
    assert session.id is not None
    ephemeral = await repo.create_saved_query(
        name="ephemeral",
        sql="SELECT id FROM metadata WHERE 0",
        entity=SavedQueryEntity.METADATA,
        session_id=session.id,
        persisted=False,
    )
    kept = await repo.create_saved_query(
        name="kept",
        sql="SELECT id FROM metadata WHERE 0",
        entity=SavedQueryEntity.ACTOR,
        session_id=session.id,
        persisted=True,
    )
    assert ephemeral.id is not None and kept.id is not None
    await repo.persist_saved_queries([kept.id])

    r = await client.get("/saved-queries", params={"session_id": session.id})
    assert r.status_code == 200
    session_ids = {i["id"] for i in r.json()["items"]}
    assert ephemeral.id in session_ids
    assert kept.id not in session_ids  # persist 后 session_id 置空

    r = await client.get("/saved-queries", params={"persisted_only": True})
    assert r.status_code == 200
    persisted_ids = {i["id"] for i in r.json()["items"]}
    assert kept.id in persisted_ids
    assert ephemeral.id not in persisted_ids
