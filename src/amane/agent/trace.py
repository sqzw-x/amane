"""`{data_dir}/agent/sessions/{id}/`. 与 DB/resources 同属 Cold data_dir, 不可当 log 清理."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import threading
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from .rows import TRACE_ROW, AguiEventRow, TraceRow, UiRow

logger = structlog.get_logger()


def session_dir(data_dir: Path, session_id: int) -> Path:
    return Path(data_dir) / "agent" / "sessions" / str(session_id)


def delete_session_dir(data_dir: Path, session_id: int) -> None:
    path = session_dir(data_dir, session_id)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


class SessionStore:
    """events.jsonl (回放行, UI 重建) + messages.json (LLM 权威历史)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._events_path = self.root / "events.jsonl"
        self._meta_path = self.root / "meta.json"
        self._messages_path = self.root / "messages.json"
        self._seq_lock = threading.Lock()
        self._rows_cache: list[TraceRow] = []
        self._rows_offset = 0
        self._rows_tail = b""
        self._rows_ino = 0
        self._rows_need_newline = self._events_need_newline()
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

    def read_events(self) -> Sequence[TraceRow]:
        """按顺序读出可解析的行; 返回值是内部缓存, 调用方不得修改.

        日志只追加, 因此解析结果按字节偏移缓存, 每次读取只解析新增的字节; 文件变短或换了一个 (inode 变化)
        则从头重读. 半行 (崩溃留下) 与未知行 (版本先行的写入方留下) 都只丢弃该行: 一行坏数据不应让整个
        会话无法回放.
        """
        try:
            stat = self._events_path.stat()
        except FileNotFoundError:
            self._reset_cache(0)
            return self._rows_cache
        if stat.st_ino != self._rows_ino or stat.st_size < self._rows_offset:
            self._reset_cache(stat.st_ino)
        if stat.st_size != self._rows_offset:
            with self._events_path.open("rb") as f:
                f.seek(self._rows_offset)
                chunk = f.read()
                self._rows_offset = f.tell()
            lines = (self._rows_tail + chunk).split(b"\n")
            self._rows_tail = lines.pop()  # 末尾未成行的残留, 等下一段补全
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._rows_cache.append(TRACE_ROW.validate_json(line))
                except ValidationError:
                    logger.warning(
                        "回放行无法解析, 已丢弃",
                        path=str(self._events_path),
                        line=line[:200].decode(errors="replace"),
                    )
        return self._rows_cache

    def _reset_cache(self, ino: int) -> None:
        self._rows_cache = []
        self._rows_offset = 0
        self._rows_tail = b""
        self._rows_ino = ino

    def _events_need_newline(self) -> bool:
        """文件是否停在没有换行符的半行上 (崩溃留下的)."""
        try:
            if self._events_path.stat().st_size == 0:
                return False
            with self._events_path.open("rb") as f:
                f.seek(-1, io.SEEK_END)
                return f.read(1) != b"\n"
        except OSError:
            return False

    def events_after(self, after: int) -> list[TraceRow]:
        """`after` 之后的行; 缓存内的行按 `seq` 递增, 缺 `seq` 的行 (手改文件的产物) 不参与跟随."""
        return [row for row in self.read_events() if (row.seq or 0) > after]

    def ui_events(self) -> list[UiRow]:
        """页面契约的行; 协议透传行只在 `POST .../agui` 通道分发, 不发给页面."""
        return [row for row in self.read_events() if not isinstance(row, AguiEventRow)]

    def _write_row(self, row: TraceRow) -> TraceRow:
        with self._seq_lock:
            self._last_seq += 1
            out = row.model_copy(update={"seq": self._last_seq})
            # 半行没有换行符: 不先补一个, 新行会与它拼成同一行, 读取时被一起丢弃
            lead = "\n" if self._rows_need_newline else ""
            self._rows_need_newline = False
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(lead + out.model_dump_json(by_alias=True) + "\n")
        self._wake.set()
        return out

    async def append_row(self, row: TraceRow) -> TraceRow:
        return self._write_row(row)

    async def follow(self, after: int) -> AsyncIterator[TraceRow]:
        """从 after 之后跟随: 先回放磁盘, 再等新事件; turn 结束且追平后停止."""
        cursor = after
        while True:
            batch = self.events_after(cursor)
            for row in batch:
                cursor = row.seq or cursor
                yield row
            if batch:
                continue  # 落盘期间又追加了行, 立刻读取下一批
            if not self._turn_running and not self.events_after(cursor):
                return
            self._wake.clear()
            if self.events_after(cursor):
                continue  # 清除前又有行落盘, 不必等待
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
