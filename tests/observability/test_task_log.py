"""task.log 尾部读取与 JSONL 解析."""

import asyncio
import json
import logging
from pathlib import Path

import pytest
import structlog

from amane.config import HotSettings
from amane.db.models import Task, TaskStatus, TaskType
from amane.events import EventBus, EventType
from amane.observability import read_task_log, setup_logging
from amane.observability.recorder import Recorder, task_dir_for


class _CapturingEventBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[EventType, dict]] = []

    async def emit(self, event_type: EventType, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _line(event: str, *, level: str = "info", logger: str = "amane.test", **extra: object) -> str:
    return json.dumps({"event": event, "level": level, "logger": logger, **extra})


def _write_log(tmp_path: Path, task_id: int, data: str) -> None:
    root = task_dir_for(tmp_path, task_id)
    root.mkdir(parents=True)
    (root / "task.log").write_text(data, encoding="utf-8")


def _reset_logging_state() -> logging.Logger:
    root = logging.getLogger("amane")
    root.handlers.clear()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    return root


def _cleanup_logging_state(root: logging.Logger) -> None:
    root.handlers.clear()
    logging.getLogger("amane.request").handlers.clear()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest.fixture
def event_bus_logging(tmp_path: Path):
    """Recorder 依赖进程级 structlog / logging 配置; 用带 WS 捕获的管线并负责清理."""
    root = _reset_logging_state()
    bus = _CapturingEventBus()
    setup_logging(level="DEBUG", event_bus=bus, log_dir=tmp_path / "app")
    yield bus
    _cleanup_logging_state(root)


def test_missing_log_returns_empty_page(tmp_path: Path):
    page = read_task_log(tmp_path, 42, limit=10)
    assert page.entries == []
    assert page.truncated is False


def test_parse_maps_fields_and_skips_bad_lines(tmp_path: Path):
    good_start = _line(
        "task started",
        logger="amane.scheduler.worker",
        timestamp="2026-11-08T00:00:00.000000Z",
        task_id=42,
        task_type="scrape",
        payload={"number": "ABC-001"},
    )
    good_end = _line("task completed", logger="worker", level="warning")
    lines = [
        good_start.encode(),
        b"",
        b"not json",
        b"[1, 2]",
        json.dumps({"level": "info"}).encode(),
        json.dumps({"event": 3}).encode(),
        b"\xff",
        good_end.encode(),
    ]
    root = task_dir_for(tmp_path, 42)
    root.mkdir(parents=True)
    (root / "task.log").write_bytes(b"\r\n".join(lines))

    page = read_task_log(tmp_path, 42, limit=10)

    assert page.truncated is False
    assert [entry.message for entry in page.entries] == ["task started", "task completed"]
    first, second = page.entries
    assert first.level == "INFO"
    assert first.source == "worker"
    assert first.timestamp == "2026-11-08T00:00:00.000000Z"
    assert first.context == {"task_id": 42, "task_type": "scrape", "payload": {"number": "ABC-001"}}
    assert second.source == "worker"
    assert second.level == "WARNING"
    assert second.timestamp is None
    assert second.context == {}


def test_tail_limit_keeps_complete_lines_across_chunk_boundary(tmp_path: Path):
    entries = [f"line-{index}-" + "x" * 200 for index in range(500)]
    _write_log(tmp_path, 42, "\n".join(_line(message) for message in entries))

    page = read_task_log(tmp_path, 42, limit=3)

    assert page.truncated is True
    assert [entry.message for entry in page.entries] == entries[-3:]

    all_page = read_task_log(tmp_path, 42, limit=len(entries))
    assert all_page.truncated is False
    assert len(all_page.entries) == len(entries)


def test_limit_must_be_positive(tmp_path: Path):
    with pytest.raises(ValueError, match="limit must be positive"):
        read_task_log(tmp_path, 42, limit=0)


def test_single_line_larger_than_chunk(tmp_path: Path):
    message = "x" * (128 * 1024)
    _write_log(tmp_path, 42, _line(message))

    page = read_task_log(tmp_path, 42, limit=10)

    assert page.truncated is False
    assert [entry.message for entry in page.entries] == [message]


def test_byte_cap_keeps_truncated_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("amane.observability.task_log._MAX_TAIL_BYTES", 512)
    _write_log(tmp_path, 42, "\n".join(_line(f"line-{index}-" + "x" * 50) for index in range(50)))

    page = read_task_log(tmp_path, 42, limit=50)

    assert page.truncated is True
    assert page.entries
    assert all(entry.message.startswith("line-") for entry in page.entries)


@pytest.mark.asyncio(loop_scope="function")
async def test_recorder_log_matches_ws_events(tmp_path: Path, event_bus_logging: _CapturingEventBus):
    """结构化和 stdlib 记录在 task.log 与 WS 事件里的时间戳与字段必须一致."""
    structlog.contextvars.bind_contextvars(task_id=42, task_type="scrape")
    task = Task(id=42, type=TaskType.SCRAPE, status=TaskStatus.RUNNING, payload={}, priority=0)
    rec = Recorder.begin(tmp_path, task, HotSettings())
    try:
        structlog.get_logger("amane.test").warning("structured", foo=1)
        logging.getLogger("amane.test").warning("stdlib", extra={"foo": 2})
        await asyncio.sleep(0.01)
    finally:
        rec.close()

    page = read_task_log(tmp_path, 42, limit=10)
    assert [entry.message for entry in page.entries] == ["structured", "stdlib"]
    events = {
        data["message"]: data
        for event_type, data in event_bus_logging.events
        if event_type is EventType.LOG and data.get("task_id") == 42
    }
    assert set(events) == {"structured", "stdlib"}
    for entry in page.entries:
        ws = events[entry.message]
        assert ws["timestamp"] == entry.timestamp
        assert ws["level"] == entry.level
        assert ws["source"] == entry.source
        assert ws["foo"] == entry.context["foo"]
