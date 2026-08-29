"""offline task-record CLI 冒烟测试."""

from pathlib import Path

import pytest

from amane.config import HotSettings
from amane.db.models import Task, TaskStatus, TaskType
from amane.enums import SiteName
from amane.observability.models import CaptureReason, RecordManifest
from amane.observability.recorder import Recorder, task_dir_for
from amane.observability.runner import run_record
from amane.parsing import ContentType


@pytest.mark.asyncio
async def test_offline_record_javdb_search_miss(tmp_path: Path):
    """用录制的空搜索页离线复现: 期望 scrape 失败 (无元数据)."""
    cases = Path(__file__).resolve().parents[1] / "crawlers" / "cases" / "javdb"
    empty = cases / "empty_search.html"
    if not empty.is_file():
        pytest.skip("javdb empty_search.html fixture missing")

    hot = HotSettings()
    for ct in hot.scraping.content_routes:
        hot.scraping.content_routes[ct] = [SiteName.JAVDB]

    task = Task(
        id=7,
        type=TaskType.SCRAPE,
        status=TaskStatus.FAILED,
        payload={
            "number": "ZZZZ-999",
            "content_type": ContentType.CENSORED,
            "use_cache": [],
        },
        error="No metadata found for ZZZZ-999",
    )
    rec = Recorder.begin(tmp_path, task, hot)
    body = empty.read_bytes()
    search_url = "https://javdb.com/search?q=ZZZZ-999&locale=zh"
    rec.record_http(
        method="GET",
        url=search_url,
        status=200,
        error=None,
        content_type="text/html",
        body=body,
        elapsed_ms=1,
    )
    rec.finalize(task, success=False, error=task.error, debug_capture=False)

    root = task_dir_for(tmp_path, 7)
    man = RecordManifest.model_validate_json((root / "manifest.json").read_text())
    assert man.http_captured is True
    assert man.capture_reason == CaptureReason.FAILURE
    assert man.record_version == 2

    code = await run_record(root, online=False)
    assert code == 1  # scrape 失败退出
