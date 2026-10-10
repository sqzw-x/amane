"""结果载荷落库契约: `TaskResult.as_dict` 的输出必须能被 `tasks.result` 列的 json.dumps 直接写出.

结果模型的字段因此只允许 JSON 原生类型; 新增结果模型时下面的表要求补一条用例.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from amane.handlers import models as handler_models
from amane.handlers.models import (
    ActorScrapeResult,
    CleanupResult,
    DeleteResult,
    EmbySyncResult,
    OrganizeResult,
    R18ImportResult,
    RefreshResult,
    RescrapeResult,
    ScanInvalidResult,
    ScrapeResult,
    UpscaleResult,
)
from amane.handlers.protocol import TaskResult
from amane.net.errors import FailureReason
from amane.observability.models import (
    OrganizeConflict,
    OrganizeConflictReason,
    SiteOutcomeKind,
    SiteOutcomeRecord,
)

RESULT_CASES = [
    pytest.param(
        ScrapeResult(
            metadata_id=7,
            field_sources={"title": "dmm"},
            failed_sites=["jav321"],
            outcomes=[
                SiteOutcomeRecord(site="javdb", outcome=SiteOutcomeKind.OK),
                SiteOutcomeRecord(
                    site="jav321",
                    outcome=SiteOutcomeKind.FAILED,
                    reason=FailureReason.NOT_FOUND,
                    http_status=404,
                    detail="HTTP 404",
                ),
            ],
        ),
        id="scrape",
    ),
    pytest.param(
        ActorScrapeResult(
            actor_id=3,
            field_sources={"image": "wikipedia"},
            failed_sites=[],
            image_count=4,
            outcomes=[SiteOutcomeRecord(site="wikipedia", outcome=SiteOutcomeKind.OK)],
        ),
        id="actor_scrape",
    ),
    pytest.param(
        OrganizeResult(
            organized=2,
            skipped=1,
            conflicted=1,
            failed=0,
            pruned_dirs=1,
            conflicts=[
                OrganizeConflict(
                    path="/lib/incoming/a.mp4", target="/lib/A/a.mp4", reason=OrganizeConflictReason.target_exists
                )
            ],
        ),
        id="organize",
    ),
    pytest.param(
        EmbySyncResult(
            actors=3,
            persons=4,
            images=2,
            updated=1,
            skipped=1,
            not_found=1,
            no_image=1,
            failed=1,
            failures=["Alice: 头像上传失败: HTTP 500"],
        ),
        id="emby_sync",
    ),
    pytest.param(RefreshResult(added=2, removed=1, scrape=2), id="refresh"),
    pytest.param(CleanupResult(files_removed=1, resources_removed=0), id="cleanup"),
    pytest.param(UpscaleResult(scanned=3, upscaled=1, skipped=1, failed=1), id="upscale"),
    pytest.param(R18ImportResult(imported=True, etag=None), id="r18_import"),
    pytest.param(RescrapeResult(submitted=5, metadata=3, actors=2), id="rescrape"),
    pytest.param(
        ScanInvalidResult(
            inventory_id="abc123",
            entries=4,
            dirs=1,
            scope_path="/lib/incoming",
            truncated=True,
            skipped_dirs=1,
            skipped_files=2,
            blocked_dirs=0,
        ),
        id="scan_invalid",
    ),
    pytest.param(
        DeleteResult(
            deleted=3,
            changed=1,
            failed=0,
            freed_bytes=1024,
            hardlink_items=2,
            pruned_dirs=1,
            excluded=1,
            indexed=4,
            reverify_rejected=1,
        ),
        id="delete",
    ),
]


class NonNativeResult(BaseModel):
    """探针: 这些类型只有 JSON 模式能转成原生值, python 模式会原样交给 json.dumps 报 TypeError."""

    type: str = "probe"
    path: Path
    when: datetime
    tags: set[str]


@pytest.mark.parametrize("result", RESULT_CASES)
def test_as_dict_is_json_serializable(result: BaseModel) -> None:
    dumped = TaskResult(success=True, result=result).as_dict()

    assert dumped is not None
    assert type(dumped["type"]) is str  # 判别标签落库后是普通字符串, 不带枚举类型
    assert json.loads(json.dumps(dumped)) == dumped


def test_as_dict_converts_non_native_fields(tmp_path: Path) -> None:
    path = tmp_path / "a.mp4"
    dumped = TaskResult(
        success=True,
        result=NonNativeResult(path=path, when=datetime(2026, 1, 1, tzinfo=UTC), tags={"srt"}),
    ).as_dict()

    assert dumped == {
        "type": "probe",
        "path": str(path),  # 路径按当前平台的写法落库
        "when": "2026-01-01T00:00:00Z",
        "tags": ["srt"],
    }


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param({"files_removed": 1}, {"files_removed": 1}, id="dict"),
        pytest.param(None, None, id="none"),
    ],
)
def test_as_dict_passthrough(result: dict[str, int] | None, expected: dict[str, int] | None) -> None:
    assert TaskResult(success=True, result=result).as_dict() == expected


def test_every_result_model_has_a_case() -> None:
    declared = {
        obj.__name__
        for obj in vars(handler_models).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__name__.endswith("Result")
    }
    covered = {type(case.values[0]).__name__ for case in RESULT_CASES}

    assert declared == covered
