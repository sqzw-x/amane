"""会话落盘与 follow 表测试."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from amane.agent.rows import TextDeltaRow
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
    row1 = await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))
    assert row1.seq == 1

    async def producer() -> None:
        await asyncio.sleep(0.05)
        await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="b"))
        store.set_turn_running(False)

    task = asyncio.create_task(producer())
    got = [row.text async for row in store.follow(0) if isinstance(row, TextDeltaRow)]
    await task
    assert got == ["a", "b"]


@pytest.mark.asyncio
async def test_session_store_row_roundtrip(tmp_path: Path) -> None:
    """落盘再读回: 行按联合还原, 且 seq 由 store 补 (重启后回放同一份数据)."""
    await SessionStore(tmp_path / "3").append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))

    row = SessionStore(tmp_path / "3").read_events()[0]
    assert isinstance(row, TextDeltaRow)
    assert (row.block_id, row.text, row.seq) == ("m1", "a", 1)
