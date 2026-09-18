"""会话落盘与 follow 表测试."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from amane.agent.cache import ResultCache
from amane.agent.service import AgentService
from amane.agent.trace import SessionStore
from amane.config import AgentConfig
from amane.db.repository import Repository


def test_session_store_roundtrip_messages(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "1")
    msgs: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="你好")])]
    store.save_messages(msgs)
    loaded = store.load_messages()
    assert loaded is not None
    assert len(loaded) == 1


@pytest.mark.asyncio
async def test_session_store_seq_and_follow(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "2")
    store.set_turn_running(True)
    row1 = await store.append_row({"type": "text_delta", "text": "a"})
    assert row1["seq"] == 1

    async def producer() -> None:
        await asyncio.sleep(0.05)
        await store.append_row({"type": "text_delta", "text": "b"})
        store.set_turn_running(False)

    task = asyncio.create_task(producer())
    got = [str(ev["text"]) async for ev in store.follow(0) if ev["type"] == "text_delta"]
    await task
    assert got == ["a", "b"]


@pytest.mark.asyncio
async def test_load_history_drops_system_prompt_parts(tmp_path: Path, repo: Repository) -> None:
    """历史里的 SystemPromptPart 不得随请求送出: 身份与规则只经 agent instructions 注入."""
    service = AgentService(
        db_path=tmp_path / "amane.db",
        data_dir=tmp_path / "data",
        repo=repo,
        cache=ResultCache(ttl_s=60, max_entries=8),
        config=AgentConfig(api_key="sk-test", model="test-model"),
    )
    session = await repo.create_agent_session(title="legacy")
    assert session.id is not None
    service.store_for(session.id).save_messages(
        [
            ModelRequest(parts=[SystemPromptPart(content="旧身份与规则"), UserPromptPart(content="旧问题")]),
            ModelResponse(parts=[TextPart(content="旧答复")]),
            ModelRequest(parts=[SystemPromptPart(content="孤立的系统提示")]),
            ModelRequest(parts=[UserPromptPart(content="新问题")]),
        ]
    )

    loaded = service._load_history(session.id)

    assert len(loaded) == 3
    first, last = loaded[0], loaded[-1]
    assert isinstance(first, ModelRequest) and isinstance(last, ModelRequest)
    assert [part.content for part in first.parts if isinstance(part, UserPromptPart)] == ["旧问题"]
    assert not any(
        isinstance(part, SystemPromptPart)
        for message in loaded
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    assert [part.content for part in last.parts if isinstance(part, UserPromptPart)] == ["新问题"]


@pytest.mark.asyncio
async def test_load_history_drops_legacy_capability_load(tmp_path: Path, repo: Repository) -> None:
    """旧版延迟载入的调用与返回成对丢弃: 其返回带着当时那版域内指令, 而该工具已不存在."""
    service = AgentService(
        db_path=tmp_path / "amane.db",
        data_dir=tmp_path / "data",
        repo=repo,
        cache=ResultCache(ttl_s=60, max_entries=8),
        config=AgentConfig(api_key="sk-test", model="test-model"),
    )
    session = await repo.create_agent_session(title="legacy-load")
    assert session.id is not None
    service.store_for(session.id).save_messages(
        [
            ModelRequest(parts=[UserPromptPart(content="载入 feed-ops")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="load_capability",
                        args={"id": "feed-ops"},
                        tool_call_id="call-load",
                        tool_kind="capability-load",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="load_capability",
                        content={"instructions": "旧域内指令"},
                        tool_call_id="call-load",
                        tool_kind="capability-load",
                    )
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="现在清理")]),
        ]
    )

    loaded = service._load_history(session.id)

    assert [type(message).__name__ for message in loaded] == ["ModelRequest", "ModelRequest"]
    assert [
        part.content
        for message in loaded
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ] == ["载入 feed-ops", "现在清理"]
