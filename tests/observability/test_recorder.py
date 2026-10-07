"""任务可观测会话 / 导出 表测试."""

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import structlog

from amane.config import HotSettings
from amane.crawlers.block import FailureReason
from amane.db.models import Task, TaskStatus, TaskType
from amane.enums import SiteName
from amane.net import reset_skip_http_body, set_skip_http_body
from amane.observability.export import build_record_zip
from amane.observability.models import SECRETS_HOT_FILENAME, CaptureReason, SiteOutcomeKind
from amane.observability.recorder import Recorder, task_dir_for


@pytest.fixture
def task() -> Task:
    return Task(
        id=42,
        type=TaskType.SCRAPE,
        status=TaskStatus.RUNNING,
        payload={"number": "SSIS-497", "content_type": "censored"},
        priority=0,
    )


def test_recorder_failure_keeps_http(tmp_path: Path, task: Task):
    hot = HotSettings()
    hot.scraping.site_config[SiteName.JAVDB].cookie = {"session": "secret"}
    rec = Recorder.begin(tmp_path, task, hot)
    rec.update_summary(eligible_sites=["javdb"], sites_queried=["javdb"])
    rec.record_site_outcome(site="javdb", outcome=SiteOutcomeKind.FAILED, reason=FailureReason.NO_USABLE_METADATA)
    structlog.contextvars.bind_contextvars(site="javdb")
    try:
        rec.record_http(
            method="GET",
            url="https://example.com/search",
            status=200,
            error=None,
            content_type="text/html",
            body=b"<html>ok</html>",
            elapsed_ms=12,
        )
    finally:
        structlog.contextvars.unbind_contextvars("site")
    task.status = TaskStatus.FAILED
    task.error = "No metadata found"
    rec.finalize(task, success=False, error=task.error, debug_capture=False)

    root = task_dir_for(tmp_path, 42)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["http_captured"] is True
    assert manifest["capture_reason"] == CaptureReason.FAILURE
    assert manifest["redacted"] is True
    assert (root / "http" / "index.jsonl").is_file()
    idx = json.loads((root / "http" / "index.jsonl").read_text().splitlines()[0])
    assert idx["site"] == "javdb"
    assert (root / "task.log").is_file()
    cfg = json.loads((root / "config.hot.json").read_text())
    assert "watcher" not in cfg
    assert "worker" not in cfg
    assert cfg["scraping"]["site_config"]["javdb"]["cookie"]["session"] == "***"
    secrets = json.loads((root / SECRETS_HOT_FILENAME).read_text())
    assert secrets["scraping"]["site_config"]["javdb"]["cookie"]["session"] == "secret"
    summary = json.loads((root / "summary.json").read_text())
    assert "error" not in summary
    assert "number" not in summary
    assert summary["sites_queried"] == ["javdb"]
    assert summary["outcomes"]["javdb"]["outcome"] == "failed"
    assert summary["outcomes"]["javdb"]["reason"] == "no_usable_metadata"


def test_recorder_omits_secrets_when_none(tmp_path: Path, task: Task):
    rec = Recorder.begin(tmp_path, task, HotSettings())
    task.status = TaskStatus.FAILED
    rec.finalize(task, success=False, error="boom", debug_capture=False)
    root = task_dir_for(tmp_path, 42)
    assert not (root / SECRETS_HOT_FILENAME).exists()
    cfg = json.loads((root / "config.hot.json").read_text())
    assert set(cfg.keys()) == {"scraping", "network", "llm", "r18", "plugins"}


def test_recorder_success_discards_http_unless_debug(tmp_path: Path, task: Task):
    rec = Recorder.begin(tmp_path, task, HotSettings())
    rec.record_http(
        method="GET",
        url="https://example.com/a",
        status=200,
        error=None,
        content_type="text/html",
        body=b"<html/>",
        elapsed_ms=1,
    )
    task.status = TaskStatus.DONE
    rec.finalize(task, success=True, error=None, debug_capture=False)
    root = task_dir_for(tmp_path, 42)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["http_captured"] is False
    assert not (root / "http").exists()


def test_recorder_success_keeps_http_with_debug(tmp_path: Path, task: Task):
    rec = Recorder.begin(tmp_path, task, HotSettings())
    rec.record_http(
        method="GET",
        url="https://example.com/a",
        status=200,
        error=None,
        content_type="text/html",
        body=b"<html/>",
        elapsed_ms=1,
    )
    task.status = TaskStatus.DONE
    rec.finalize(task, success=True, error=None, debug_capture=True)
    root = task_dir_for(tmp_path, 42)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["http_captured"] is True
    assert manifest["capture_reason"] == CaptureReason.DEBUG_FLAG


def test_build_record_zip_redacted_by_default(tmp_path: Path, task: Task):
    hot = HotSettings()
    hot.llm.api_key = "sk-secret"
    rec = Recorder.begin(tmp_path, task, hot)
    task.status = TaskStatus.FAILED
    rec.finalize(task, success=False, error="boom", debug_capture=False)

    data = build_record_zip(tmp_path, 42, include_secrets=False)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert all(SECRETS_HOT_FILENAME not in n for n in names)
        cfg = json.loads(zf.read("task-42/config.hot.json"))
        assert cfg["llm"]["api_key"] == "***"
        man = json.loads(zf.read("task-42/manifest.json"))
        assert man["redacted"] is True


def test_build_record_zip_include_secrets(tmp_path: Path, task: Task):
    hot = HotSettings()
    hot.llm.api_key = "sk-secret"
    rec = Recorder.begin(tmp_path, task, hot)
    task.status = TaskStatus.FAILED
    rec.finalize(task, success=False, error="boom", debug_capture=False)

    data = build_record_zip(tmp_path, 42, include_secrets=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        cfg = json.loads(zf.read("task-42/config.hot.json"))
        assert cfg["llm"]["api_key"] == "sk-secret"
        man = json.loads(zf.read("task-42/manifest.json"))
        assert man["redacted"] is False


def test_record_http_skip_body(tmp_path: Path, task: Task):
    rec = Recorder.begin(tmp_path, task, HotSettings())
    token = set_skip_http_body(True)
    try:
        rec.record_http(
            method="GET",
            url="https://cdn.example/img.jpg",
            status=200,
            error=None,
            content_type="image/jpeg",
            body=b"\xff\xd8" + b"\x00" * 100,
            elapsed_ms=5,
        )
    finally:
        reset_skip_http_body(token)
    assert rec._exchanges[0].body is None
    assert rec._exchanges[0].meta.body_file is None
    rec.close()


def test_record_site_outcome_merge_keeps_specific_reason(tmp_path: Path, task: Task):
    """拦截原因先行上报后, 引擎后续上报不覆盖具体 reason / outcome 只升级不降级."""
    rec = Recorder.begin(tmp_path, task, HotSettings())
    # 拦截检测先行: 具体原因
    rec.record_site_outcome(
        site="javdb",
        outcome=SiteOutcomeKind.FAILED,
        reason=FailureReason.CLOUDFLARE_CHALLENGE,
        http_status=None,
        detail="https://javdb.com/search",
    )
    # 引擎后续上报: 无可用元数据 (不应覆盖具体原因)
    rec.record_site_outcome(site="javdb", outcome=SiteOutcomeKind.FAILED, reason=FailureReason.NO_USABLE_METADATA)
    # 缓存命中先于失败? 不可能 — 命中不调 fetch; 这里验证 failed 不会被后续 ok 降级
    rec.record_site_outcome(site="javdb", outcome=SiteOutcomeKind.OK)

    out = rec.summary.outcomes["javdb"]
    assert out.outcome == SiteOutcomeKind.FAILED
    assert out.reason == FailureReason.CLOUDFLARE_CHALLENGE
    assert out.detail == "https://javdb.com/search"
    rec.close()


def test_record_site_outcome_cache_then_failed_upgrades(tmp_path: Path, task: Task):
    """同站点结果合并: cache_hit < ok < failed, 后上报的更差结果升级 outcome."""
    rec = Recorder.begin(tmp_path, task, HotSettings())
    rec.record_site_outcome(site="dmm", outcome=SiteOutcomeKind.CACHE_HIT)
    assert rec.summary.outcomes["dmm"].outcome == SiteOutcomeKind.CACHE_HIT
    rec.record_site_outcome(site="dmm", outcome=SiteOutcomeKind.OK)
    assert rec.summary.outcomes["dmm"].outcome == SiteOutcomeKind.OK
    rec.record_site_outcome(site="dmm", outcome=SiteOutcomeKind.FAILED, reason=FailureReason.TIMEOUT)
    out = rec.summary.outcomes["dmm"]
    assert out.outcome == SiteOutcomeKind.FAILED
    assert out.reason == FailureReason.TIMEOUT
    rec.close()


def test_recorder_begin_discards_stale_summary(tmp_path: Path):
    """任务 id 复用时 begin 丢弃目录里残留的刮削摘要, 避免 ORGANIZE 读到旧 summary.json."""
    leftover = Task(id=7, type=TaskType.ORGANIZE, status=TaskStatus.RUNNING, payload={"library_id": 1})
    root = task_dir_for(tmp_path, 7)
    root.mkdir(parents=True)
    (root / "summary.json").write_text(
        json.dumps(
            {
                "sites_queried": ["javdb"],
                "outcomes": {"javdb": {"site": "javdb", "outcome": "ok"}},
            }
        ),
        encoding="utf-8",
    )
    rec = Recorder.begin(tmp_path, leftover, HotSettings())
    rec.close()
    assert not (root / "summary.json").is_file()
