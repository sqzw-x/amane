"""/tasks 端点测试"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType
from amane.enums import DownloadableResource

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestListTasks:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_empty(self, client: AsyncClient, stop_worker: None):
        empty = await client.get("tasks")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0


class TestSubmitTask:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_submit_payloads_and_rejects(
        self, client: AsyncClient, repo: Repository, safe_path, seed_library: Repository
    ):
        lib = await repo.create_library(name="t", path=str(safe_path), recursive=False, patterns=["*.mp4"])
        refresh = await client.post("tasks", json={"type": "refresh", "library_id": lib.id})
        assert refresh.status_code == 202
        payload = refresh.json()["payload"]
        assert payload["path"] == str(safe_path)
        assert payload["recursive"] is False
        assert payload["patterns"] == ["*.mp4"]
        assert payload["library_id"] == lib.id
        assert payload["scan"] == ["add"]
        assert payload["scrape"] == ["pending"]
        assert sorted(payload["use_cache"]) == ["metadata", "trans"]

        overridden = await client.post(
            "tasks", json={"type": "refresh", "library_id": lib.id, "recursive": False, "scan": ["remove"]}
        )
        assert overridden.status_code == 202
        assert overridden.json()["payload"]["scan"] == ["remove"]

        org = await client.post("tasks", json={"type": "organize", "library_id": lib.id})
        assert org.status_code == 202
        assert org.json()["payload"]["write_nfo"] is True
        assert set(org.json()["payload"]["copy_resources"]) == {"thumb", "poster", "extrafanart", "trailer"}

        lib2 = await repo.create_library(name="o", path=str(safe_path / "o"), write_nfo=True)
        (safe_path / "o").mkdir()
        over_org = await client.post(
            "tasks",
            json={"type": "organize", "library_id": lib2.id, "write_nfo": False, "copy_resources": ["thumb", "poster"]},
        )
        assert over_org.json()["payload"]["write_nfo"] is False
        assert over_org.json()["payload"]["copy_resources"] == ["thumb", "poster"]

        lib3 = await repo.create_library(
            name="i",
            path=str(safe_path / "i"),
            write_nfo=False,
            copy_resources=[DownloadableResource.thumb],
        )
        (safe_path / "i").mkdir()
        inherited = await client.post("tasks", json={"type": "organize", "library_id": lib3.id})
        assert inherited.json()["payload"]["write_nfo"] is False
        assert inherited.json()["payload"]["copy_resources"] == ["thumb"]

        scrape = await client.post("tasks", json={"type": "scrape", "number": "MIDV-123"})
        assert scrape.status_code == 202
        assert scrape.json()["payload"]["number"] == "MIDV-123"
        assert sorted(scrape.json()["payload"]["use_cache"]) == ["metadata", "trans"]
        assert scrape.json()["payload"]["content_type"] == "censored"
        for number, expected in (
            ("FC2-PPV-1234567", "fc2"),
            ("vixen.23.04.15", "western"),
            ("MD-0123", "chinese"),
            ("MIDV-123", "censored"),
            ("VIDEO", "western"),
        ):
            inferred = await client.post("tasks", json={"type": "scrape", "number": number})
            assert inferred.json()["payload"]["content_type"] == expected
        forced = await client.post(
            "tasks", json={"type": "scrape", "number": "FC2-PPV-1234567", "content_type": "censored"}
        )
        assert forced.json()["payload"]["content_type"] == "censored"

        media = await seed_library.create_media_file(library_id=1, path="/media/里番/MD-0123.mp4")
        assert media.id is not None
        hentai = await client.post("tasks", json={"type": "scrape", "media_id": media.id})
        assert hentai.json()["payload"]["content_type"] == "hentai"
        await seed_library.update_media_file(media.id, number="MIDV-123")
        cached = await client.post("tasks", json={"type": "scrape", "media_id": media.id, "use_cache": ["trans"]})
        assert cached.json()["payload"]["use_cache"] == ["trans"]

        override = await client.post("tasks", json={"type": "scrape", "media_id": media.id, "number": "MIDV-123"})
        assert override.status_code == 202
        assert override.json()["payload"]["number"] == "MIDV-123"
        assert override.json()["payload"]["media_file_id"] == media.id
        assert override.json()["payload"]["content_type"] == "censored"
        forced_override = await client.post(
            "tasks",
            json={
                "type": "scrape",
                "media_id": media.id,
                "number": "MIDV-123",
                "content_type": "western",
            },
        )
        assert forced_override.json()["payload"]["content_type"] == "western"
        blank = await client.post("tasks", json={"type": "scrape", "media_id": media.id, "number": "   "})
        assert blank.status_code == 202
        assert blank.json()["payload"]["number"] == "MD-0123"
        assert blank.json()["payload"]["content_type"] == "hentai"
        missing = await client.post("tasks", json={"type": "scrape", "media_id": 9999, "number": "MIDV-123"})
        assert missing.status_code == 404
        assert (await client.post("tasks", json={"type": "scrape", "number": "   "})).status_code == 422

        cleanup = await client.post("tasks", json={"type": "cleanup"})
        assert cleanup.status_code == 202
        assert cleanup.json()["payload"]["remove_missing_files"] is True
        custom = await client.post(
            "tasks",
            json={"type": "cleanup", "remove_missing_files": False, "remove_unreferenced_resources": False},
        )
        assert custom.json()["payload"]["remove_missing_files"] is False
        upscale = await client.post("tasks", json={"type": "upscale", "limit": 10})
        assert upscale.status_code == 202
        assert upscale.json()["payload"]["limit"] == 10

        assert (await client.post("tasks", json={"type": "unknown"})).status_code == 422
        assert (await client.post("tasks", json={"library_id": 1})).status_code == 422
        assert (await client.post("tasks", json={"type": "refresh"})).status_code == 422
        assert (await client.post("tasks", json={"type": "refresh", "library_id": 9999})).status_code == 404
        assert (await client.post("tasks", json={"type": "organize", "library_id": 9999})).status_code == 404

        schema = await client.get("tasks/schema")
        assert schema.status_code == 200
        covered = set(schema.json()["discriminator"]["mapping"].keys())
        missing = set(TaskType) - covered
        assert not missing, f"TaskSubmission missing: {missing}. Add submission model to TaskSubmission union."

    @pytest.mark.asyncio(loop_scope="function")
    async def test_submit_organize_reuses_active(
        self, client: AsyncClient, repo: Repository, safe_path, stop_worker: None
    ):
        lib = await repo.create_library(name="t", path=str(safe_path))
        first = await client.post("tasks", json={"type": "organize", "library_id": lib.id})
        second = await client.post("tasks", json={"type": "organize", "library_id": lib.id, "write_nfo": False})
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        listed = await repo.list_tasks(task_types=[TaskType.ORGANIZE])
        assert len(listed) == 1


class TestGetTask:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_and_titles(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"n": "ABC-001", "number": "MIDV-123"})
        resp = await client.get(f"tasks/{task.id}")
        assert resp.status_code == 200
        assert resp.json()["payload"]["n"] == "ABC-001"
        assert resp.json()["title"] == "MIDV-123"
        assert (await client.get("tasks/9999")).status_code == 404

        await repo.upsert_metadata(number="T-1", actors=["Taro"])
        actors = (await client.get("facets/actor")).json()["items"]
        actor_id = next(a["id"] for a in actors if a["name"] == "Taro")
        scrape = await client.post("tasks", json={"type": "actor_scrape", "actor_id": actor_id})
        assert scrape.status_code == 202
        assert scrape.json()["title"] == "Taro"
        listed = await client.get("tasks")
        assert any(i["title"] == "MIDV-123" for i in listed.json()["items"])
        cleanup = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert (await client.get(f"tasks/{cleanup.id}")).json()["title"] is None


class TestBatchTasks:
    """POST /tasks/batch 接线. 计数/跳过链/重试见 tests/db/test_task_batch.py."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_http(self, client: AsyncClient, repo: Repository, stop_worker: None):
        assert (
            await client.post("tasks/batch", json={"action": "delete", "task_ids": [1], "status": ["done"]})
        ).status_code == 422

        t = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert t.id is not None
        await repo.complete_task(t.id)
        deleted = await client.post("tasks/batch", json={"action": "delete", "task_ids": [t.id, 999_999]})
        assert deleted.status_code == 200
        assert deleted.json()["affected"] == 1
        assert deleted.json()["missing"] == 1

        queued = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert queued.id is not None
        cancelled = await client.post("tasks/batch", json={"action": "cancel", "task_ids": [queued.id]})
        assert cancelled.status_code == 200
        assert cancelled.json()["affected"] == 1

        failed = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "X"})
        assert failed.id is not None
        await repo.fail_task(failed.id, error="x")
        retried = await client.post("tasks/batch", json={"action": "retry", "task_ids": [failed.id]})
        assert retried.status_code == 200
        assert retried.json()["submitted"] == 1


class TestTaskWorker:
    """GET/POST /tasks/worker — 暂停领队, 不取消运行中任务."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_pause_resume(self, client: AsyncClient, app: FastAPI):
        resp = await client.get("tasks/worker")
        assert resp.status_code == 200
        assert resp.json()["paused"] is False
        paused = await client.post("tasks/worker/pause")
        assert paused.json()["paused"] is True
        assert app.state.runtime.worker.is_paused is True
        resumed = await client.post("tasks/worker/resume")
        assert resumed.json()["paused"] is False
        assert app.state.runtime.worker.is_paused is False


class TestTaskReport:
    """GET /tasks/{id}/report - 读 log 目录摘要 + 终态 result."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_http(self, app: FastAPI, client: AsyncClient, repo: Repository, stop_worker: None):
        assert (await client.get("tasks/9999/report")).status_code == 404
        queued = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-497"})
        assert (await client.get(f"tasks/{queued.id}/report")).status_code == 409

        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "jur-837"})
        assert task.id is not None
        await repo.fail_task(task.id, error="No metadata found for jur-837")
        log_dir = app.state.runtime.config.cold.log_dir
        root = log_dir / "tasks" / f"task-{task.id}"
        root.mkdir(parents=True)
        (root / "summary.json").write_text(
            '{"eligible_sites":["dmm","javdb"],"sites_queried":["dmm","javdb"],"outcomes":{'
            '"dmm":{"site":"dmm","outcome":"failed","reason":"no_usable_metadata"},'
            '"javdb":{"site":"javdb","outcome":"failed","reason":"http_error","http_status":403,"detail":"HTTP 403"}}}',
            encoding="utf-8",
        )
        resp = await client.get(f"tasks/{task.id}/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["headline"] == "No metadata found for jur-837"
        by_site = {o["site"]: o for o in body["outcomes"]}
        assert by_site["javdb"]["http_status"] == 403
        assert by_site["dmm"]["reason"] == "no_usable_metadata"

        done = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-001"})
        assert done.id is not None
        await repo.complete_task(done.id, result={"metadata_id": 17, "field_sources": {}, "failed_sites": []})
        done_body = (await client.get(f"tasks/{done.id}/report")).json()
        assert done_body["metadata_id"] == 17
        assert done_body["actor_id"] is None

        actor = await repo.create_task(task_type=TaskType.ACTOR_SCRAPE, payload={"actor_id": 8})
        assert actor.id is not None
        await repo.fail_task(actor.id, error="Actor 8 not found")
        actor_body = (await client.get(f"tasks/{actor.id}/report")).json()
        assert actor_body["actor_id"] == 8
        assert actor_body["headline"] == "Actor 8 not found"


class TestTaskChain:
    """GET /tasks 装饰 child_count; /children 的 JSON. 链语义见 tests/db/test_task_links.py."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_chain_http(self, client: AsyncClient, repo: Repository, stop_worker: None):
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        children = await repo.complete_task_with_followups(
            claimed.id,
            result={},
            followups=[
                ("scrape:1", TaskType.SCRAPE, {"number": "MIDV-123"}, 0),
                ("scrape:2", TaskType.SCRAPE, {"number": "MIDV-124"}, 0),
            ],
        )
        child_ids = {c.id for c in children}
        standalone = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert standalone.id is not None

        listed = await client.get("tasks")
        by_id = {item["id"]: item for item in listed.json()["items"]}
        assert parent.id in by_id
        assert child_ids.isdisjoint(by_id)
        assert by_id[parent.id]["child_count"] == 2
        assert by_id[parent.id]["child_status"]["queued"] == 2
        assert by_id[standalone.id]["child_count"] == 0

        by_root = await client.get(f"tasks?root_task_id={parent.id}")
        assert {item["id"] for item in by_root.json()["items"]} == {parent.id, *child_ids}

        kids = await client.get(f"tasks/{parent.id}/children")
        assert {item["id"] for item in kids.json()["items"]} == child_ids
        assert kids.json()["total"] == 2
        assert {item["link_key"] for item in kids.json()["items"]} == {"scrape:1", "scrape:2"}

        page = await client.get(f"tasks/{parent.id}/children?limit=1&offset=0")
        assert len(page.json()["items"]) == 1
        assert page.json()["total"] == 2
        assert (await client.get("tasks/9999/children")).status_code == 404


class TestTaskRecord:
    """GET /tasks/{id}/record - 导出任务记录"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_record_queued_rejected(self, client: AsyncClient, repo: Repository, stop_worker: None):
        assert (await client.get("tasks/9999/record")).status_code == 404
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-497"})
        resp = await client.get(f"tasks/{task.id}/record")
        assert resp.status_code == 409

    @pytest.mark.asyncio(loop_scope="function")
    async def test_record_after_finished_task(self, client: AsyncClient, safe_path: Path):
        scan_dir = safe_path / "record_videos"
        scan_dir.mkdir()
        lib = (await client.post("libraries", json={"path": str(scan_dir)})).json()
        resp = await client.post("tasks", json={"type": "refresh", "library_id": lib["id"], "scan": ["add"]})
        task_id = resp.json()["id"]

        for _ in range(50):
            check = await client.get(f"tasks/{task_id}")
            if check.json()["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)

        resp = await client.get(f"tasks/{task_id}/record")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/zip"
        assert resp.content[:2] == b"PK"
