"""测试 RESCRAPE handler - 按目标扇出影片 / 演员补刮."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from amane.db import ActorSortField, Repository, TaskType
from amane.handlers import RescrapeHandler, RescrapePayload, RescrapeTarget


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_corpus(repo: Repository):
    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload())
    assert result.success
    assert result.result is not None
    assert result.result.submitted == 0
    assert result.result.metadata == 0
    assert result.result.actors == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_fans_out_oldest_first_with_limit(repo: Repository):
    for number in ("A-001", "B-002", "C-003", "D-004"):
        await repo.upsert_metadata(number=number)

    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload(limit=2))
    assert result.result is not None
    assert result.result.submitted == 2
    assert result.result.metadata == 2
    assert result.result.actors == 0

    followups = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
    # 取最久未更新的 2 条 (A/B 先插入, updated_at 更早)
    assert {f.payload["number"] for f in followups} == {"A-001", "B-002"}
    for f in followups:
        assert f.priority == -1
        assert set(f.payload["use_cache"]) == {"metadata", "trans"}


@pytest.mark.asyncio(loop_scope="function")
async def test_content_type_inference(repo: Repository):
    lib = await repo.create_library(name="L", path="/media")
    assert lib.id is not None
    await repo.upsert_metadata(number="MIDV-123")  # 无文件 → 番号级 censored
    await repo.upsert_metadata(number="FC2-PPV-1234567")  # 无文件 → fc2
    hentai_id = await repo.upsert_metadata(number="MD-0123")
    assert hentai_id.id is not None
    await repo.create_media_file(lib.id, path="/media/里番/MD-0123.mp4", metadata_id=hentai_id.id)  # 路径优先 → hentai
    await repo.upsert_metadata(number="MD-0456")  # 无文件 → 国产番号模式 chinese

    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload(limit=10))
    assert result.result is not None
    assert result.result.submitted == 4

    followups = [f for f in (result.followups or []) if f.task_type == TaskType.SCRAPE]
    by_number = {f.payload["number"]: f.payload["content_type"] for f in followups}
    assert by_number == {
        "MIDV-123": "censored",
        "FC2-PPV-1234567": "fc2",
        "MD-0123": "hentai",
        "MD-0456": "chinese",
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_min_age_days_computes_cutoff(repo: Repository, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    async def fake_list_metadata(**kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(repo, "list_metadata", fake_list_metadata)
    handler = RescrapeHandler(repo)

    result = await handler.handle(RescrapePayload(min_age_days=7))
    assert result.result is not None
    assert result.result.submitted == 0
    before = captured["updated_before"]
    assert before is not None
    delta = datetime.now(UTC) - before
    assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, hours=1)

    # 未设门槛时不传 updated_before
    captured.clear()
    await handler.handle(RescrapePayload())
    assert captured["updated_before"] is None


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_target_fans_out_oldest_first(repo: Repository):
    for number, name in (("A-001", "Act-A"), ("B-002", "Act-B"), ("C-003", "Act-C")):
        await repo.upsert_metadata(number=number, actors=[name])
    by_name = {a.name: a.id for a in await repo.get_actors_by_names(["Act-A", "Act-B", "Act-C"])}

    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload(limit=2, targets={RescrapeTarget.actor}))
    assert result.result is not None
    assert result.result.submitted == 2
    assert result.result.metadata == 0
    assert result.result.actors == 2

    followups = result.followups or []
    assert all(f.task_type == TaskType.ACTOR_SCRAPE for f in followups)
    assert {f.payload["actor_id"] for f in followups} == {by_name["Act-A"], by_name["Act-B"]}
    for f in followups:
        assert f.priority == -1
        assert f.key == f"actor-scrape:{f.payload['actor_id']}"
        assert set(f.payload["use_cache"]) == {"metadata", "trans"}


@pytest.mark.asyncio(loop_scope="function")
async def test_both_targets_apply_limit_independently(repo: Repository):
    for number, name in (("A-001", "Act-A"), ("B-002", "Act-B"), ("C-003", "Act-C")):
        await repo.upsert_metadata(number=number, actors=[name])
    by_name = {a.name: a.id for a in await repo.get_actors_by_names(["Act-A", "Act-B", "Act-C"])}

    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload(limit=2, targets={RescrapeTarget.metadata, RescrapeTarget.actor}))
    assert result.result is not None
    assert result.result.metadata == 2
    assert result.result.actors == 2
    assert result.result.submitted == 4

    followups = result.followups or []
    scrape = [f for f in followups if f.task_type == TaskType.SCRAPE]
    actors = [f for f in followups if f.task_type == TaskType.ACTOR_SCRAPE]
    assert {f.payload["number"] for f in scrape} == {"A-001", "B-002"}
    assert {f.payload["actor_id"] for f in actors} == {by_name["Act-A"], by_name["Act-B"]}


@pytest.mark.asyncio(loop_scope="function")
async def test_default_targets_skips_actors(repo: Repository):
    await repo.upsert_metadata(number="A-001", actors=["Act-A"])
    handler = RescrapeHandler(repo)
    result = await handler.handle(RescrapePayload(limit=10))
    assert result.result is not None
    assert result.result.metadata == 1
    assert result.result.actors == 0
    assert all(f.task_type == TaskType.SCRAPE for f in (result.followups or []))


@pytest.mark.asyncio(loop_scope="function")
async def test_actor_min_age_days_computes_cutoff(repo: Repository, monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    async def fake_list_actors(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(repo, "list_actors", fake_list_actors)
    handler = RescrapeHandler(repo)
    await handler.handle(RescrapePayload(min_age_days=3, targets={RescrapeTarget.actor}))
    assert captured["sort_by"] == ActorSortField.UPDATED_AT
    before = captured["updated_before"]
    assert before is not None
    delta = datetime.now(UTC) - before
    assert timedelta(days=2, hours=23) <= delta <= timedelta(days=3, hours=1)


def test_legacy_schedule_payload_defaults_to_metadata():
    payload = RescrapePayload.model_validate({"type": "rescrape", "limit": 10})
    assert payload.targets == {RescrapeTarget.metadata}
    assert payload.limit == 10


# --- 非法 payload (边界用例) ---

INVALID_PAYLOADS = [
    ({"limit": 0}, "limit"),
    ({"limit": 1001}, "limit"),
    ({"min_age_days": 0}, "min_age_days"),
    ({"min_age_days": -3}, "min_age_days"),
    ({"targets": []}, "targets"),
]


@pytest.mark.parametrize("data,field", INVALID_PAYLOADS)
def test_payload_rejects_invalid(data: dict, field: str):
    with pytest.raises(ValidationError, match=field):
        RescrapePayload(**data)
