"""会话落盘与 follow 表测试."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from amane.agent.rows import TextDeltaRow, ToolCallRow
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
async def test_session_store_skips_unreadable_rows(tmp_path: Path) -> None:
    """无法解析的行与未知类型只丢弃该行: 一行坏数据不应让整个会话无法回放."""
    store = SessionStore(tmp_path / "4")
    await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))
    with store._events_path.open("a", encoding="utf-8") as f:
        f.write('{"type":"text_delta","block_id":"m1","text":"半')
        f.write("\n")
        f.write('{"type":"未来的行","payload":1}\n')
    await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="b"))

    rows = SessionStore(tmp_path / "4").read_events()
    assert [row.text for row in rows if isinstance(row, TextDeltaRow)] == ["a", "b"]
    assert [row.seq for row in rows] == [1, 2]  # store 自己写的行连续


def _texts(store: SessionStore) -> list[str]:
    return [row.text for row in store.read_events() if isinstance(row, TextDeltaRow)]


@pytest.mark.asyncio
async def test_session_store_parses_only_appended_bytes(tmp_path: Path) -> None:
    """按偏移增量解析: 连续读取得到累积结果, 半行在补全后才成行, 文件被截断则从头重读."""
    store = SessionStore(tmp_path / "5")
    assert store.read_events() == []

    await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))
    assert _texts(store) == ["a"]

    with store._events_path.open("a", encoding="utf-8") as f:
        f.write('{"type":"text_delta","block_id":"m1","text":"b"')  # 半行
    assert _texts(store) == ["a"]

    with store._events_path.open("a", encoding="utf-8") as f:
        f.write("}\n")
    assert _texts(store) == ["a", "b"]

    # 换了文件 (inode 变化) 且比原来更长: 只有 inode 判定能发现, 必须从头重读而不是从旧偏移续读
    moved = store._events_path.with_suffix(".moved")
    moved.write_text(json.dumps({"type": "text_delta", "block_id": "m1", "text": "c" * 200}) + "\n", encoding="utf-8")
    moved.replace(store._events_path)
    assert _texts(store) == ["c" * 200]

    store._events_path.write_text("")
    assert store.read_events() == []


@pytest.mark.asyncio
async def test_session_store_appends_after_partial_line(tmp_path: Path) -> None:
    """日志末尾停在没有换行的半行时, 新进程追加的行必须自成一行, 不与半行拼成同一行."""
    path = tmp_path / "6"
    path.mkdir()
    await SessionStore(path).append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))
    with (path / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"type":"text_delta","block_id":"m1","text":"半')  # 无换行

    restarted = SessionStore(path)
    await restarted.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="b"))

    assert _texts(restarted) == ["a", "b"]


@pytest.mark.asyncio
async def test_session_store_row_roundtrip(tmp_path: Path) -> None:
    """落盘再读回: 行按判别联合还原成各自的类型, seq 由 store 补, at 是合法时间戳."""
    store = SessionStore(tmp_path / "3")
    await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="a"))
    await store.append_row(
        ToolCallRow(type="tool_call", tool_call_id="c1", name="sql_explore", args={"sql": "SELECT 1"})
    )

    rows = SessionStore(tmp_path / "3").read_events()
    assert [type(row) for row in rows] == [TextDeltaRow, ToolCallRow]
    assert [row.seq for row in rows] == [1, 2]
    assert {datetime.fromisoformat(row.at).tzinfo for row in rows} == {UTC}
    assert isinstance(rows[0], TextDeltaRow)
    assert (rows[0].block_id, rows[0].text) == ("m1", "a")
    assert isinstance(rows[1], ToolCallRow)
    assert (rows[1].tool_call_id, rows[1].name, rows[1].args) == ("c1", "sql_explore", {"sql": "SELECT 1"})
