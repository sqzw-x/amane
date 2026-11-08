"""按需读取 ``task.log`` 末尾若干行, 解析为与 WebSocket LOG 事件同形的条目."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import TaskLogEntry, TaskLogPage
from .recorder import task_dir_for

_TAIL_CHUNK_SIZE = 64 * 1024
_MAX_TAIL_BYTES = 8 * 1024 * 1024
_RESERVED_KEYS = frozenset({"event", "logger", "level", "timestamp"})
_DEFAULT_SOURCE = "server"
_DEFAULT_LEVEL = "INFO"


def _read_tail_lines(path: Path, limit: int) -> tuple[list[bytes], bool]:
    """从文件末尾向前按块读, 直到覆盖 ``limit`` 行; 返回原始行与是否省略了更早的行."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        chunks: list[bytes] = []
        newlines = 0
        remaining_bytes = _MAX_TAIL_BYTES
        while pos > 0 and newlines <= limit and remaining_bytes > 0:
            size = min(_TAIL_CHUNK_SIZE, pos, remaining_bytes)
            pos -= size
            remaining_bytes -= size
            f.seek(pos)
            chunk = f.read(size)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
        data = b"".join(reversed(chunks))
        # 块的起点可能落在行中间; 只有前一个字节是换行时首行才完整.
        starts_mid_line = pos > 0
        if starts_mid_line:
            f.seek(pos - 1)
            starts_mid_line = f.read(1) != b"\n"

    lines = data.splitlines()
    if starts_mid_line and lines:
        lines = lines[1:]
    return lines[-limit:], pos > 0 or len(lines) > limit


def _parse_line(line: bytes) -> TaskLogEntry | None:
    """坏行 (非 JSON / 非对象 / 无 event) 直接跳过, 不因为一行坏数据丢掉整份日志."""
    try:
        obj = json.loads(line)
    except UnicodeDecodeError, json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    message = obj.get("event")
    if not isinstance(message, str):
        return None

    level = obj.get("level")
    logger_name = obj.get("logger")
    timestamp = obj.get("timestamp")
    return TaskLogEntry(
        timestamp=timestamp if isinstance(timestamp, str) else None,
        level=level.upper() if isinstance(level, str) else _DEFAULT_LEVEL,
        source=logger_name.rsplit(".", 1)[-1] if isinstance(logger_name, str) and logger_name else _DEFAULT_SOURCE,
        message=message,
        context={key: value for key, value in obj.items() if key not in _RESERVED_KEYS},
    )


def read_task_log(log_dir: Path, task_id: int, *, limit: int) -> TaskLogPage:
    """任务目录不存在或 ``task.log`` 缺失时返回空页, 不视为错误."""
    if limit < 1:
        raise ValueError("limit must be positive")
    path = task_dir_for(log_dir, task_id) / "task.log"
    if not path.is_file():
        return TaskLogPage()
    try:
        lines, truncated = _read_tail_lines(path, limit)
    except FileNotFoundError:
        return TaskLogPage()
    entries = [entry for line in lines if (entry := _parse_line(line)) is not None]
    return TaskLogPage(entries=entries, truncated=truncated)
