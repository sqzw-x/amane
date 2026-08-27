"""/tasks 端点测试"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskStatus, TaskType
from amane.enums import DownloadableResource

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestListTasks:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_filters(self, client: AsyncClient, repo: Repository, stop_worker: None):
        empty = await client.get("tasks")
        assert empty.status_code == 200
        assert empty.json()["items"] == []

        await repo.create_task(task_type=TaskType.SCRAPE, payload={"x": 1})
        task2 = await repo.create_task(task_type=TaskType.REFRESH, payload={})
        listed = await client.get("tasks")
        assert listed.json()["total"] == 2

        await repo.claim_next_task()
        queued = await client.get("tasks?status=queued")
        assert len(queued.json()["items"]) == 1
        assert queued.json()["items"][0]["id"] == task2.id

        by_type = await client.get("tasks?type=refresh")
        assert len(by_type.json()["items"]) == 1
        assert by_type.json()["items"][0]["type"] == "refresh"

        for _ in range(5):
            await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        limited = await client.get("tasks?limit=3")
        assert len(limited.json()["items"]) == 3


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
    """POST /tasks/batch — cancel / delete / retry, 按 ID 或筛选."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_ids_and_filter_rejected(self, client: AsyncClient):
        resp = await client.post("tasks/batch", json={"action": "delete", "task_ids": [1], "status": ["done"]})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_by_ids(self, client: AsyncClient, repo: Repository, stop_worker: None):
        ids: list[int] = []
        for _ in range(3):
            t = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
            assert t.id is not None
            await repo.complete_task(t.id)
            ids.append(t.id)
        queued = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert queued.id is not None
        resp = await client.post("tasks/batch", json={"action": "delete", "task_ids": [*ids, queued.id, 999_999]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["affected"] == 3
        assert body["skipped"] == 1
        assert body["missing"] == 1
        for tid in ids:
            assert await repo.get_task(tid) is None
        assert await repo.get_task(queued.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_by_filter_status(self, client: AsyncClient, repo: Repository, stop_worker: None):
        done = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        failed = await repo.create_task(task_type=TaskType.REFRESH, payload={})
        queued = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert done.id is not None and failed.id is not None and queued.id is not None
        await repo.complete_task(done.id)
        await repo.fail_task(failed.id, error="x")
        resp = await client.post("tasks/batch", json={"action": "delete", "status": ["done"]})
        assert resp.json()["affected"] == 1
        assert await repo.get_task(done.id) is None
        assert await repo.get_task(failed.id) is not None
        assert await repo.get_task(queued.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_by_filter_skips_active_chain(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """清除已完成: 有排队后裔的链根跳过, 其它 DONE 仍删."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        await repo.complete_task_with_followups(
            claimed.id, result={}, followups=[("scrape:1", TaskType.SCRAPE, {"number": "LIVE"}, 0)]
        )
        lone = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert lone.id is not None
        await repo.complete_task(lone.id)

        resp = await client.post("tasks/batch", json={"action": "delete", "status": ["done"]})
        body = resp.json()
        assert body["affected"] == 1
        assert body["skipped"] >= 1
        assert await repo.get_task(lone.id) is None
        assert await repo.get_task(parent.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_by_filter_type(self, client: AsyncClient, repo: Repository, stop_worker: None):
        scrape = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        refresh = await repo.create_task(task_type=TaskType.REFRESH, payload={})
        assert scrape.id is not None and refresh.id is not None
        await repo.complete_task(scrape.id)
        await repo.complete_task(refresh.id)
        resp = await client.post("tasks/batch", json={"action": "delete", "type": ["scrape"]})
        assert resp.json()["affected"] == 1
        assert await repo.get_task(scrape.id) is None
        assert await repo.get_task(refresh.id) is not None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_removes_log_file(self, app: FastAPI, client: AsyncClient, repo: Repository):
        t = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert t.id is not None
        rel = f"tasks/task-{t.id}/task.log"
        await repo.update_task_log_file(t.id, rel)
        await repo.complete_task(t.id)
        log_dir = app.state.runtime.config.cold.log_dir
        log_path = log_dir / rel
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('{"event": "x"}\n', encoding="utf-8")
        resp = await client.post("tasks/batch", json={"action": "delete", "task_ids": [t.id]})
        assert resp.status_code == 200
        assert resp.json()["affected"] == 1
        assert not log_path.exists()
        assert not log_path.parent.exists()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_queued_by_ids(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert task.id is not None
        resp = await client.post("tasks/batch", json={"action": "cancel", "task_ids": [task.id]})
        assert resp.status_code == 200
        assert resp.json()["affected"] == 1
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED
        assert updated.error == "Cancelled by user"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_skips_terminal(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        assert task.id is not None
        await repo.complete_task(task.id)
        resp = await client.post("tasks/batch", json={"action": "cancel", "task_ids": [task.id]})
        assert resp.json() == {"affected": 0, "skipped": 1, "missing": 0, "submitted": 0, "task_ids": []}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_missing_id(self, client: AsyncClient):
        resp = await client.post("tasks/batch", json={"action": "cancel", "task_ids": [9999]})
        assert resp.json()["missing"] == 1
        assert resp.json()["affected"] == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_running_fallback(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert task.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == task.id
        resp = await client.post("tasks/batch", json={"action": "cancel", "task_ids": [task.id]})
        assert resp.json()["affected"] == 1
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED
        assert "Cancelled by user" in (updated.error or "")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_by_filter(self, client: AsyncClient, repo: Repository, stop_worker: None):
        a = await repo.create_task(task_type=TaskType.SCRAPE, payload={})
        b = await repo.create_task(task_type=TaskType.REFRESH, payload={})
        assert a.id is not None and b.id is not None
        resp = await client.post("tasks/batch", json={"action": "cancel", "type": ["scrape"]})
        assert resp.json()["affected"] == 1
        ra = await repo.get_task(a.id)
        rb = await repo.get_task(b.id)
        assert ra is not None and ra.status == TaskStatus.FAILED
        assert rb is not None and rb.status == TaskStatus.QUEUED

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retry_failed_by_ids(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "MIDV-123"})
        assert task.id is not None
        await repo.fail_task(task.id, error="test failure")
        resp = await client.post("tasks/batch", json={"action": "retry", "task_ids": [task.id]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["affected"] == 1
        assert body["submitted"] == 1
        assert len(body["task_ids"]) == 1
        new_id = body["task_ids"][0]
        assert new_id != task.id
        created = await repo.get_task(new_id)
        assert created is not None
        assert created.status == TaskStatus.QUEUED
        assert created.type == TaskType.SCRAPE
        assert created.payload == {"number": "MIDV-123"}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retry_skips_non_failed(self, client: AsyncClient, repo: Repository, stop_worker: None):
        task = await repo.create_task(task_type=TaskType.CLEANUP, payload={})
        assert task.id is not None
        resp = await client.post("tasks/batch", json={"action": "retry", "task_ids": [task.id]})
        assert resp.json()["skipped"] == 1
        assert resp.json()["affected"] == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retry_by_filter(self, client: AsyncClient, repo: Repository, stop_worker: None):
        a = await repo.create_task(task_type=TaskType.SCRAPE, payload={"n": 1})
        b = await repo.create_task(task_type=TaskType.SCRAPE, payload={"n": 2})
        assert a.id is not None and b.id is not None
        await repo.fail_task(a.id, error="x")
        await repo.complete_task(b.id)
        resp = await client.post("tasks/batch", json={"action": "retry", "status": ["failed"]})
        assert resp.json()["affected"] == 1
        assert resp.json()["submitted"] == 1


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
    """GET /tasks/{id}/report - 任务结果摘要"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_nonexistent(self, client: AsyncClient):
        resp = await client.get("tasks/9999/report")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_queued_rejected(self, client: AsyncClient, repo: Repository, stop_worker):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-497"})
        resp = await client.get(f"tasks/{task.id}/report")
        assert resp.status_code == 409

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_failed_with_summary(self, app: FastAPI, client: AsyncClient, repo: Repository, stop_worker):
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
        assert by_site["javdb"] == {
            "site": "javdb",
            "outcome": "failed",
            "reason": "http_error",
            "http_status": 403,
            "detail": "HTTP 403",
        }
        assert by_site["dmm"]["outcome"] == "failed"
        assert by_site["dmm"]["reason"] == "no_usable_metadata"
        assert body["metadata_id"] is None
        assert body["actor_id"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_done_with_metadata_id(self, client: AsyncClient, repo: Repository, stop_worker):
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-001"})
        assert task.id is not None
        await repo.complete_task(task.id, result={"metadata_id": 17, "field_sources": {}, "failed_sites": []})

        resp = await client.get(f"tasks/{task.id}/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metadata_id"] == 17
        assert body["actor_id"] is None
        assert body["headline"] is None
        assert body["outcomes"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_done_with_actor_id(self, client: AsyncClient, repo: Repository, stop_worker):
        task = await repo.create_task(task_type=TaskType.ACTOR_SCRAPE, payload={"actor_id": 8})
        assert task.id is not None
        await repo.complete_task(
            task.id, result={"actor_id": 8, "image_count": 3, "field_sources": {}, "failed_sites": []}
        )

        resp = await client.get(f"tasks/{task.id}/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["actor_id"] == 8
        assert body["metadata_id"] is None
        assert body["headline"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_report_failed_actor_scrape_keeps_actor_id(self, client: AsyncClient, repo: Repository, stop_worker):
        task = await repo.create_task(task_type=TaskType.ACTOR_SCRAPE, payload={"actor_id": 8})
        assert task.id is not None
        await repo.fail_task(task.id, error="Actor 8 not found")

        resp = await client.get(f"tasks/{task.id}/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["actor_id"] == 8
        assert body["headline"] == "Actor 8 not found"


class TestTaskChain:
    """任务链: root 过滤与后继边查询 (前端任务树渲染)."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_chain_by_root(self, client: AsyncClient, repo: Repository, stop_worker):
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        children = await repo.complete_task_with_followups(
            claimed.id,
            result={"added": 1},
            followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )
        assert len(children) == 1
        child = children[0]
        assert child.id is not None

        resp = await client.get(f"tasks?root_task_id={parent.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {item["id"] for item in body["items"]} == {parent.id, child.id}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_defaults_to_roots_only(self, client: AsyncClient, repo: Repository, stop_worker):
        """GET /tasks 默认只返回链根任务, 子任务不混入顶层列表."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        children = await repo.complete_task_with_followups(
            claimed.id,
            result={},
            followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )
        child = children[0]
        assert child.id is not None

        resp = await client.get("tasks")
        assert resp.status_code == 200
        body = resp.json()
        ids = {item["id"] for item in body["items"]}
        assert parent.id in ids
        assert child.id not in ids
        assert body["total"] == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_marks_child_counts(self, client: AsyncClient, repo: Repository, stop_worker):
        """有后继的根任务 child_count/child_status 反映直接子任务; 无后继的为零."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        await repo.complete_task_with_followups(
            claimed.id,
            result={},
            followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )
        standalone = await repo.create_task(task_type=TaskType.CLEANUP, payload={})

        resp = await client.get("tasks")
        by_id = {item["id"]: item for item in resp.json()["items"]}
        assert by_id[parent.id]["child_count"] == 1
        assert by_id[parent.id]["child_status"] == {
            "queued": 1,
            "running": 0,
            "done": 0,
            "failed": 0,
        }
        assert by_id[standalone.id]["child_count"] == 0
        assert by_id[standalone.id]["child_status"] == {
            "queued": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_children_endpoint(self, client: AsyncClient, repo: Repository, stop_worker):
        """GET /tasks/{id}/children 返回直接子任务, 每条带 link_key."""
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

        resp = await client.get(f"tasks/{parent.id}/children")
        assert resp.status_code == 200
        body = resp.json()
        assert {item["id"] for item in body["items"]} == child_ids
        assert body["total"] == 2
        assert {item["link_key"] for item in body["items"]} == {"scrape:1", "scrape:2"}
        assert all(item["child_count"] == 0 for item in body["items"])

    @pytest.mark.asyncio(loop_scope="function")
    async def test_children_total_is_untruncated(self, client: AsyncClient, repo: Repository, stop_worker):
        """limit 截断 items, total 仍是出边总数."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        followups: list[tuple[str, TaskType, dict[str, object], int]] = [
            (f"scrape:{i}", TaskType.SCRAPE, {"number": f"N-{i}"}, 0) for i in range(5)
        ]
        await repo.complete_task_with_followups(claimed.id, result={}, followups=followups)

        resp = await client.get(f"tasks/{parent.id}/children?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5

        page2 = await client.get(f"tasks/{parent.id}/children?limit=2&offset=2")
        assert len(page2.json()["items"]) == 2
        assert page2.json()["total"] == 5

    @pytest.mark.asyncio(loop_scope="function")
    async def test_children_nonexistent_task(self, client: AsyncClient):
        resp = await client.get("tasks/9999/children")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_filter_status_queued_sees_children(self, client: AsyncClient, repo: Repository, stop_worker):
        """status=queued 时: 父已 DONE、子 SCRAPE 排队中 → 根行应出现在列表 (筛选匹配子任务)."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        children = await repo.complete_task_with_followups(
            claimed.id,
            result={},
            followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )
        child = children[0]
        assert child.id is not None

        resp = await client.get("tasks?status=queued")
        assert resp.status_code == 200
        body = resp.json()
        ids = {item["id"] for item in body["items"]}
        assert parent.id in ids
        assert body["total"] == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_filter_type_scrape_sees_children(self, client: AsyncClient, repo: Repository, stop_worker):
        """type=scrape 时: fan-out 的子 SCRAPE 对应根行出现在列表."""
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 1})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        assert claimed.id is not None
        await repo.complete_task_with_followups(
            claimed.id,
            result={},
            followups=[("scrape", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )
        standalone = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "STD-1"})
        assert standalone.id is not None

        resp = await client.get("tasks?type=scrape")
        assert resp.status_code == 200
        body = resp.json()
        ids = {item["id"] for item in body["items"]}
        assert parent.id in ids
        assert standalone.id in ids
        assert body["total"] == 2


class TestTaskRecord:
    """GET /tasks/{id}/record - 导出任务记录"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_record_nonexistent_task(self, client: AsyncClient):
        resp = await client.get("tasks/9999/record")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_record_queued_rejected(self, client: AsyncClient, repo: Repository, stop_worker):
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
