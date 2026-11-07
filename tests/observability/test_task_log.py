"""task.log 尾部读取与 JSONL 解析."""

import json
from pathlib import Path

import pytest

from amane.observability import read_task_log
from amane.observability.recorder import task_dir_for


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
