"""task.log 尾部读取与 JSONL 解析."""

import json
import logging
from pathlib import Path

import pytest
import structlog

from amane.config import HotSettings
from amane.db.models import Task, TaskStatus, TaskType
from amane.observability import read_task_log, setup_logging
from amane.observability.recorder import Recorder, task_dir_for


@pytest.fixture
def logging_pipeline(tmp_path: Path):
    """Recorder 依赖进程级 structlog / logging 配置; 每个用到的用例独享一份并清理."""
    root = logging.getLogger("amane")
    root.handlers.clear()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    setup_logging(level="DEBUG", log_dir=tmp_path / "app")
    yield
    root.handlers.clear()
    logging.getLogger("amane.request").handlers.clear()
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def _write_log(tmp_path: Path, task_id: int, data: str) -> None:
    root = task_dir_for(tmp_path, task_id)
    root.mkdir(parents=True)
    (root / "task.log").write_text(data, encoding="utf-8")


def test_missing_log_returns_empty_page(tmp_path: Path):
    page = read_task_log(tmp_path, 42, limit=10)
    assert page.entries == []
    assert page.truncated is False


def test_parse_maps_fields_and_skips_bad_lines(tmp_path: Path):
    good_start = {
        "event": "task started",
        "task_id": 42,
        "task_type": "scrape",
        "logger": "amane.scheduler.worker",
        "level": "info",
        "timestamp": "2026-11-08T00:00:00.000000Z",
        "payload": {"number": "ABC-001"},
    }
    good_end = {"event": "task completed", "logger": "worker", "level": "warning"}
    lines = [
        json.dumps(good_start),
        "",
        "not json",
        "[1, 2]",
        json.dumps({"level": "info"}),
        json.dumps({"event": 3}),
        json.dumps(good_end),
    ]
    _write_log(tmp_path, 42, "\n".join(lines))

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


def test_invalid_utf8_line_is_skipped(tmp_path: Path):
    root = task_dir_for(tmp_path, 42)
    root.mkdir(parents=True)
    valid = json.dumps({"event": "ok", "level": "info", "logger": "x"}).encode()
    (root / "task.log").write_bytes(valid + b"\n\xff\n" + valid)

    page = read_task_log(tmp_path, 42, limit=10)

    assert [entry.message for entry in page.entries] == ["ok", "ok"]


def test_tail_limit_keeps_complete_lines_across_chunk_boundary(tmp_path: Path):
    entries = [{"event": f"line-{index}-" + "x" * 200, "level": "info", "logger": "amane.test"} for index in range(500)]
    _write_log(tmp_path, 42, "\n".join(json.dumps(entry) for entry in entries))

    page = read_task_log(tmp_path, 42, limit=3)

    assert page.truncated is True
    assert [entry.message for entry in page.entries] == [f"line-{index}-" + "x" * 200 for index in range(497, 500)]

    all_page = read_task_log(tmp_path, 42, limit=len(entries))
    assert all_page.truncated is False
    assert len(all_page.entries) == len(entries)


def test_exact_limit_is_not_truncated(tmp_path: Path):
    _write_log(
        tmp_path, 42, "\n".join(json.dumps({"event": f"line-{i}", "level": "info", "logger": "x"}) for i in range(3))
    )

    page = read_task_log(tmp_path, 42, limit=3)

    assert page.truncated is False
    assert len(page.entries) == 3


def test_limit_must_be_positive(tmp_path: Path):
    with pytest.raises(ValueError, match="limit must be positive"):
        read_task_log(tmp_path, 42, limit=0)


def test_recorder_task_log_matches_ws_shape(tmp_path: Path, logging_pipeline: None):
    """真实 Recorder 产出的 task.log 必须与 WS log 事件使用同一套字段."""
    task = Task(id=42, type=TaskType.SCRAPE, status=TaskStatus.RUNNING, payload={}, priority=0)
    structlog.contextvars.bind_contextvars(task_id=42, task_type="scrape")
    rec = Recorder.begin(tmp_path, task, HotSettings())
    try:
        structlog.get_logger("amane.test").warning("structured", foo=1)
        logging.getLogger("amane.test").warning("stdlib", extra={"foo": 2})
    finally:
        rec.close()

    page = read_task_log(tmp_path, 42, limit=10)

    assert [entry.message for entry in page.entries] == ["structured", "stdlib"]
    structured, stdlib = page.entries
    assert structured.level == "WARNING"
    assert structured.source == "test"
    assert structured.timestamp is not None
    assert structured.context["foo"] == 1
    assert structured.context["task_id"] == 42
    assert stdlib.level == "WARNING"
    assert stdlib.source == "test"
    assert stdlib.timestamp is not None
    assert stdlib.context["foo"] == 2


def test_crlf_lines_are_parsed(tmp_path: Path):
    root = task_dir_for(tmp_path, 42)
    root.mkdir(parents=True)
    line = json.dumps({"event": "line", "level": "info", "logger": "amane.test"})
    (root / "task.log").write_bytes((line + "\r\n").encode() * 3)

    page = read_task_log(tmp_path, 42, limit=10)

    assert [entry.message for entry in page.entries] == ["line", "line", "line"]


def test_single_line_larger_than_chunk(tmp_path: Path):
    message = "x" * (128 * 1024)
    _write_log(tmp_path, 42, json.dumps({"event": message, "level": "info", "logger": "amane.test"}))

    page = read_task_log(tmp_path, 42, limit=10)

    assert page.truncated is False
    assert [entry.message for entry in page.entries] == [message]


def test_byte_cap_keeps_truncated_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("amane.observability.task_log._MAX_TAIL_BYTES", 512)
    lines = [
        json.dumps({"event": f"line-{index}-" + "x" * 50, "level": "info", "logger": "amane.test"})
        for index in range(50)
    ]
    _write_log(tmp_path, 42, "\n".join(lines))

    page = read_task_log(tmp_path, 42, limit=50)

    assert page.truncated is True
    assert page.entries
    assert all(entry.message.startswith("line-") for entry in page.entries)
