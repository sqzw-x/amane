"""外部智能体接口: 工具目录与派发.

工具定义复用助理工具层, 这里验证对外契约: 名字、Schema、错误映射与无会话的交付语义.
"""

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import tomli_w
from httpx2 import ASGITransport, AsyncClient

from amane.api.app import create_app
from amane.api.routes import API_PREFIX
from tests.api.conftest import hot_for_tests
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from fastapi import FastAPI

TOKEN = "secret-token"


def _make_token_app(tmp_path: Path) -> FastAPI:
    """开启 token 的应用: 用于验证「只有引导端点公开」."""
    data_dir = tmp_path / "data"
    for path in (data_dir, tmp_path / "files", tmp_path / "logs"):
        path.mkdir(parents=True, exist_ok=True)
    copy_schema(data_dir / "amane.db")
    (data_dir / "config.toml").write_text(tomli_w.dumps(hot_for_tests().model_dump(mode="json", exclude_none=True)))

    os.environ["AMANE_DATA_DIR"] = str(data_dir)
    os.environ["AMANE_SAFE_DIRS"] = str(tmp_path / "files")
    os.environ["AMANE_LOG_DIR"] = str(tmp_path / "logs")
    os.environ["AMANE_TOKEN"] = TOKEN
    return create_app()


@pytest_asyncio.fixture
async def token_app(tmp_path: Path) -> AsyncGenerator[tuple[FastAPI, Path]]:
    """开启 token 的应用; 调用方自建客户端以分别访问带 / 不带 /api 前缀的路径."""
    app = _make_token_app(tmp_path)
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    yield app, tmp_path / "data"
    await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_catalog_lists_every_tool(client: AsyncClient) -> None:
    resp = await client.get("tools")
    assert resp.status_code == 200
    body = resp.json()

    names = [tool["name"] for tool in body["tools"]]
    assert names == sorted(names), "目录按名字排序, 便于逐字比对"
    assert len(names) == len(set(names)), "工具名是对外契约, 不允许重复"

    assert {"sql_explore", "sql_deliver", "inspect_result"} <= set(names)
    assert {"update_metadata", "delete_metadata", "submit_task", "create_library"} <= set(names)

    for tool in body["tools"]:
        assert tool["group"], f"{tool['name']} 缺少工具组"
        assert tool["params"].get("type") == "object", f"{tool['name']} 参数不是对象 Schema"
        assert tool["description"], f"{tool['name']} 缺少描述"

    assert body["schema_doc"].startswith("Read-only SQLite schema")


@pytest.mark.asyncio
async def test_catalog_reports_runtime_paths(client: AsyncClient, app: FastAPI) -> None:
    """路径供智能体自行读日志与配置, 必须与运行中的实例一致."""
    resp = await client.get("tools")
    paths = resp.json()["paths"]

    cold = app.state.runtime.config.cold
    assert paths["data_dir"] == str(cold.data_dir.resolve())
    assert paths["db_path"] == str(cold.db_path.resolve())
    assert paths["log_dir"] == str(cold.log_dir.resolve())
    assert "AMANE_TOKEN" in paths["env"]


@pytest.mark.asyncio
async def test_unknown_tool_is_404(client: AsyncClient) -> None:
    resp = await client.post("tools/no_such_tool", json={"args": {}})
    assert resp.status_code == 404
    assert "no_such_tool" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_args_are_422(client: AsyncClient) -> None:
    """缺必填字段时回字段路径, 客户端不必猜."""
    resp = await client.post("tools/update_metadata", json={"args": {"patch": {}}})
    assert resp.status_code == 422
    assert "metadata_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_tool_error_stays_200(client: AsyncClient) -> None:
    """工具自身的 error 是结果而非传输失败, 与助理看到的形态一致."""
    resp = await client.post("tools/sql_explore", json={"args": {"sql": "SELECT nope FROM metadata"}})
    assert resp.status_code == 200
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_read_tool_returns_rows(client: AsyncClient) -> None:
    resp = await client.post("tools/sql_explore", json={"args": {"sql": "SELECT COUNT(*) AS n FROM metadata"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["n"]
    assert body["row_count"] == 1


@pytest.mark.asyncio
async def test_destructive_tool_runs_without_approval(client: AsyncClient) -> None:
    """外部通道没有可等用户点击的界面, 因此不设审批闸门."""
    resp = await client.post("tools/delete_metadata", json={"args": {"metadata_id": 999}})
    assert resp.status_code == 200
    assert resp.json() == {"error": "metadata 999 不存在"}


@pytest.mark.asyncio
async def test_deliver_is_persisted_and_session_free(client: AsyncClient) -> None:
    """无会话交付的预设直接进入预设列表, 不随会话清理."""
    resp = await client.post(
        "tools/sql_deliver",
        json={"args": {"sql": "SELECT id FROM metadata", "entity": "metadata", "name": "外部交付"}},
    )
    assert resp.status_code == 200
    saved_query_id = resp.json()["saved_query_id"]

    presets = await client.get("saved-queries", params={"persisted_only": True})
    listed = {item["id"]: item for item in presets.json()["items"]}
    assert saved_query_id in listed
    assert listed[saved_query_id]["session_id"] is None


@pytest.mark.asyncio
async def test_explore_view_is_not_persisted(client: AsyncClient) -> None:
    """探查视图只按 id 取行, 不进预设列表."""
    resp = await client.post(
        "tools/sql_explore",
        json={"args": {"sql": "SELECT id FROM metadata", "create_view": True}},
    )
    assert resp.status_code == 200
    view_id = resp.json()["saved_query_id"]
    assert view_id is not None

    presets = await client.get("saved-queries", params={"persisted_only": True})
    assert view_id not in {item["id"] for item in presets.json()["items"]}

    rows = await client.post("tools/inspect_result", json={"args": {"saved_query_id": view_id}})
    assert rows.status_code == 200
    assert rows.json()["columns"] == ["id"]


@pytest.mark.asyncio
async def test_guide_is_public_and_constant(token_app: tuple[FastAPI, Path]) -> None:
    """引导端点免鉴权, 且响应是常量: 两次请求逐字相同, 不含任何实例数据."""
    app, data_dir = token_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/public/agent")
        second = await client.get("/public/agent")

    assert first.status_code == 200, "引导端点必须免鉴权"
    assert first.text == second.text
    assert TOKEN not in first.text
    assert str(data_dir) not in first.text, "公开端点不得泄露实例路径"
    assert "amane skill install" in first.text


@pytest.mark.asyncio
async def test_tools_require_token(token_app: tuple[FastAPI, Path]) -> None:
    """派发端点仍需 token: 只有引导端点公开."""
    app, _ = token_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://test{API_PREFIX}/") as client:
        assert (await client.get("tools")).status_code == 401
        assert (await client.post("tools/sql_explore", json={"args": {"sql": "SELECT 1"}})).status_code == 401
