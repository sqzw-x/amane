"""会话落盘与 follow 表测试."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from amane.agent.trace import SessionStore


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
