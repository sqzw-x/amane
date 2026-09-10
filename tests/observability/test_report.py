"""build_task_report 表测试."""

import json
from pathlib import Path

import pytest

from amane.crawlers.block import FailureReason
from amane.db.models import Task, TaskStatus, TaskType
from amane.observability.models import SiteOutcomeKind
from amane.observability.recorder import task_dir_for
from amane.observability.report import build_task_report

# 新格式 (RECORD_VERSION 2) summary: outcomes 为站点结果表, 报告直接投影同一结构.
_FIXTURE_OUTCOMES = {
    "dmm": {"site": "dmm", "outcome": "failed", "reason": "no_usable_metadata"},
    "javdb": {"site": "javdb", "outcome": "failed", "reason": "http_error", "http_status": 403, "detail": "HTTP 403"},
    "official": {"site": "official", "outcome": "failed", "reason": "no_usable_metadata"},
    "javbus": {"site": "javbus", "outcome": "failed", "reason": "not_found", "http_status": 404, "detail": "HTTP 404"},
}


def _write_jur837_fixture(log_dir: Path, task_id: int) -> None:
    root = task_dir_for(log_dir, task_id)
    root.mkdir(parents=True)
    (root / "summary.json").write_text(
        json.dumps(
            {
                "eligible_sites": ["javdb", "dmm", "javbus", "official", "r18dev"],
                "sites_queried": ["dmm", "javdb", "official", "javbus"],
                "outcomes": _FIXTURE_OUTCOMES,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("site", "reason", "http_status"),
    [
        ("dmm", FailureReason.NO_USABLE_METADATA, None),
        ("javdb", FailureReason.HTTP_ERROR, 403),
        ("official", FailureReason.NO_USABLE_METADATA, None),
        ("javbus", FailureReason.NOT_FOUND, 404),
    ],
)
def test_build_task_report_jur837_style(tmp_path: Path, site: str, reason: FailureReason, http_status: int | None):
    task = Task(
        id=1,
        type=TaskType.SCRAPE,
        status=TaskStatus.FAILED,
        payload={"number": "jur-837"},
        error="No metadata found for jur-837",
    )
    _write_jur837_fixture(tmp_path, 1)
    report = build_task_report(tmp_path, task)
    assert report.headline == "No metadata found for jur-837"
    by_site = {o.site: o for o in report.outcomes}
    assert "r18dev" not in by_site  # eligible 未调度不出现
    assert by_site[site].outcome == SiteOutcomeKind.FAILED
    assert by_site[site].reason == reason
    assert by_site[site].http_status == http_status
    # detail 为一手结构化字段, 原样投影 (不解析)
    if http_status is not None:
        assert by_site[site].detail == f"HTTP {http_status}"


def test_build_task_report_legacy_summary_falls_back(tmp_path: Path):
    """旧格式 (RECORD_VERSION 1) summary 降级: 纯字段映射, 不解析文本."""
    root = task_dir_for(tmp_path, 1)
    root.mkdir(parents=True)
    (root / "summary.json").write_text(
        json.dumps(
            {
                "eligible_sites": ["javdb", "dmm"],
                "sites_queried": ["dmm", "javdb"],
                "failed_sites": ["dmm", "javdb"],
                "cache_hits": [],
            }
        ),
        encoding="utf-8",
    )
    task = Task(id=1, type=TaskType.SCRAPE, status=TaskStatus.FAILED, payload={}, error="boom")
    report = build_task_report(tmp_path, task)
    by_site = {o.site: o for o in report.outcomes}
    assert by_site["javdb"].outcome == SiteOutcomeKind.FAILED
    assert by_site["javdb"].reason == FailureReason.NO_USABLE_METADATA
    assert by_site["javdb"].detail is None


def test_build_task_report_without_disk(tmp_path: Path):
    task = Task(
        id=9,
        type=TaskType.SCRAPE,
        status=TaskStatus.FAILED,
        payload={},
        error="boom",
    )
    report = build_task_report(tmp_path, task)
    assert report.headline == "boom"
    assert report.outcomes == []
    assert report.metadata_id is None
    assert report.actor_id is None


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"metadata_id": 42, "field_sources": {}}, 42),
        ({"metadata_id": 0}, None),
        ({"metadata_id": True}, None),
        ({"metadata_id": "7"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_build_task_report_metadata_id(tmp_path: Path, result: object, expected: int | None):
    task = Task(
        id=3,
        type=TaskType.SCRAPE,
        status=TaskStatus.DONE,
        payload={"number": "SSIS-001"},
        result=result if isinstance(result, dict) else None,
    )
    report = build_task_report(tmp_path, task)
    assert report.metadata_id == expected
    assert report.headline is None
    assert report.actor_id is None


@pytest.mark.parametrize(
    ("result", "payload", "expected"),
    [
        ({"actor_id": 9, "image_count": 2}, {"actor_id": 9}, 9),
        (None, {"actor_id": 4}, 4),
        ({"actor_id": 0}, {"actor_id": 4}, 4),
        ({"actor_id": True}, {"actor_id": 4}, 4),
        ({"actor_id": "7"}, {"actor_id": 4}, 4),
        ({}, {}, None),
        (None, {"actor_id": True}, None),
    ],
)
def test_build_task_report_actor_id(tmp_path: Path, result: object, payload: dict[str, object], expected: int | None):
    task = Task(
        id=3,
        type=TaskType.ACTOR_SCRAPE,
        status=TaskStatus.DONE,
        payload=payload,
        result=result if isinstance(result, dict) else None,
    )
    report = build_task_report(tmp_path, task)
    assert report.actor_id == expected
    assert report.metadata_id is None


@pytest.mark.parametrize(
    "task_type",
    [
        TaskType.ORGANIZE,
        TaskType.ARCHIVE,
        TaskType.REFRESH,
        TaskType.CLEANUP,
        TaskType.UPSCALE,
        TaskType.R18_IMPORT,
        TaskType.RESCRAPE,
    ],
)
def test_build_task_report_ignores_summary_for_non_scrape(tmp_path: Path, task_type: TaskType):
    """站点 outcomes 只对 SCRAPE / ACTOR_SCRAPE 投影; 其它类型即使目录里有刮削摘要也返回空."""
    task = Task(
        id=1,
        type=task_type,
        status=TaskStatus.DONE,
        payload={"library_id": 1},
        error=None,
    )
    _write_jur837_fixture(tmp_path, 1)
    report = build_task_report(tmp_path, task)
    assert report.outcomes == []
    assert report.headline is None
    assert report.metadata_id is None


def test_build_task_report_actor_scrape_keeps_outcomes(tmp_path: Path):
    task = Task(id=1, type=TaskType.ACTOR_SCRAPE, status=TaskStatus.DONE, payload={"actor_id": 3})
    _write_jur837_fixture(tmp_path, 1)
    report = build_task_report(tmp_path, task)
    assert {o.site for o in report.outcomes} == {"dmm", "javdb", "official", "javbus"}
    assert report.actor_id == 3
    assert report.metadata_id is None
