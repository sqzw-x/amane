"""`{data_dir}/agent/sessions/{id}/`. 与 DB/resources 同属 Cold data_dir, 不可当 log 清理."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TraceEvent:
    """写入时展平为带 seq 的 JSONL 行."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=_utcnow_iso)


def session_dir(data_dir: Path, session_id: int) -> Path:
    return Path(data_dir) / "agent" / "sessions" / str(session_id)


def delete_session_dir(data_dir: Path, session_id: int) -> None:
    path = session_dir(data_dir, session_id)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


class SessionStore:
    """events.jsonl (UI 回放) + messages.json (LLM 权威历史)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._events_path = self.root / "events.jsonl"
        self._meta_path = self.root / "meta.json"
        self._messages_path = self.root / "messages.json"
        self._seq_lock = threading.Lock()
        self._last_seq = self._scan_last_seq()
        # 进程重启后无后台任务; 纠正陈旧 turn_running
        if bool(self.read_meta().get("turn_running", False)):
            self._turn_running = False
            meta = self.read_meta()
            meta["turn_running"] = False
            self.write_meta(meta)
        else:
            self._turn_running = False
        self._wake = asyncio.Event()

    def _scan_last_seq(self) -> int:
        last = 0
        if not self._events_path.is_file():
            return 0
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            seq = row.get("seq")
            if isinstance(seq, int) and seq > last:
                last = seq
        return last

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def turn_running(self) -> bool:
        return self._turn_running

    def set_turn_running(self, running: bool) -> None:
        self._turn_running = running
        meta = self.read_meta()
        meta["turn_running"] = running
        self.write_meta(meta)
        self._wake.set()

    def write_meta(self, meta: dict[str, Any]) -> None:
        self._meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_meta(self) -> dict[str, Any]:
        if not self._meta_path.is_file():
            return {}
        return json.loads(self._meta_path.read_text(encoding="utf-8"))

    def read_events(self) -> list[dict[str, Any]]:
        if not self._events_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def events_after(self, after: int) -> list[dict[str, Any]]:
        return [e for e in self.read_events() if isinstance(e.get("seq"), int) and e["seq"] > after]

    def _write_row(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._seq_lock:
            self._last_seq += 1
            out = {**row, "seq": self._last_seq, "at": row.get("at") or _utcnow_iso()}
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
        self._wake.set()
        return out

    async def append_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._write_row(row)

    def append(self, event: TraceEvent) -> None:
        self._write_row({"type": event.type, **event.payload, "at": event.at})

    async def follow(self, after: int) -> AsyncIterator[dict[str, Any]]:
        """从 after 之后跟随: 先回放磁盘, 再等新事件; turn 结束且追平后停止."""
        cursor = after
        while True:
            batch = self.events_after(cursor)
            for ev in batch:
                cursor = int(ev["seq"])
                yield ev
            if not self._turn_running and not self.events_after(cursor):
                return
            self._wake.clear()
            if self.events_after(cursor) or (not self._turn_running and not self.events_after(cursor)):
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                continue

    def save_messages(self, messages: list[ModelMessage]) -> None:
        raw = ModelMessagesTypeAdapter.dump_json(messages)
        tmp = self._messages_path.with_suffix(".json.tmp")
        tmp.write_bytes(raw)
        tmp.replace(self._messages_path)

    def load_messages(self) -> list[ModelMessage] | None:
        if not self._messages_path.is_file():
            return None
        data = self._messages_path.read_bytes()
        if not data.strip():
            return None
        return list(ModelMessagesTypeAdapter.validate_json(data))


SessionTrace = SessionStore
