"""/tasks 端点测试"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, get_args

import pytest
from pydantic import BaseModel

from amane.api.models.tasks import TaskResultPayload
from amane.db.models import TaskType
from amane.enums import DownloadableResource
from amane.handlers import models as handler_models

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
        assert "recursive" not in org.json()["payload"]
        assert "patterns" not in org.json()["payload"]

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
            "tasks", json={"type": "cleanup", "remove_missing_files": False, "remove_unreferenced_resources": False}
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
        # 已移除的类型不再被接受; 删除只认后端产出的清单标识.
        assert (await client.post("tasks", json={"type": "trash", "library_id": lib.id})).status_code == 422
        assert (await client.post("tasks", json={"type": "delete", "library_id": lib.id})).status_code == 422
        assert (
            await client.post("tasks", json={"type": "delete", "library_id": lib.id, "inventory_id": "nope"})
        ).status_code == 202
        assert (await client.post("tasks", json={"type": "scan_invalid", "library_id": 9999})).status_code == 404
        ids_and_path = await client.post(
            "tasks", json={"type": "organize", "library_id": lib.id, "path": str(safe_path), "media_file_ids": [1]}
        )
        assert ids_and_path.status_code == 422
        other_lib = await repo.create_library(name="x", path=str(safe_path / "x"))
        (safe_path / "x").mkdir()
        assert other_lib.id is not None
        foreign = await repo.create_media_file(library_id=other_lib.id, path=str(safe_path / "x" / "a.mp4"))
        assert foreign.id is not None
        foreign_ids = await client.post(
            "tasks", json={"type": "organize", "library_id": lib.id, "media_file_ids": [foreign.id]}
        )
        assert foreign_ids.status_code == 422

        schema = await client.get("tasks/schema")
        assert schema.status_code == 200
        covered = set(schema.json()["discriminator"]["mapping"].keys())
        missing = set(TaskType) - covered
        assert not missing, f"TaskSubmission missing: {missing}. Add submission model to TaskSubmission union."

    @pytest.mark.asyncio(loop_scope="function")
    async def test_submit_organize_creates_new_when_active(
        self, client: AsyncClient, repo: Repository, safe_path, stop_worker: None
    ):
        lib = await repo.create_library(name="t", path=str(safe_path))
        first = await client.post("tasks", json={"type": "organize", "library_id": lib.id})
        second = await client.post("tasks", json={"type": "organize", "library_id": lib.id, "write_nfo": False})
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] != second.json()["id"]
        listed = await repo.list_tasks(task_types=[TaskType.ORGANIZE])
        assert len(listed) == 2


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


class TestSubmitEmbySync:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_submission_resolves_payload(self, client: AsyncClient, stop_worker: None):
        """提交联合与 resolve_submission 的 emby_sync 分支: 手动触发路径."""
        resp = await client.post("tasks", json={"type": "emby_sync", "actor_id": 7, "force": True})

        assert resp.status_code == 202
        assert resp.json()["payload"] == {"actor_id": 7, "force": True}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_title_is_actor_name_only_for_single_actor(
        self, client: AsyncClient, repo: Repository, stop_worker: None
    ):
        await repo.upsert_metadata(number="T-EMBY", actors=["Taro"])
        actors = (await client.get("facets/actor")).json()["items"]
        actor_id = next(a["id"] for a in actors if a["name"] == "Taro")

        single = await client.post("tasks", json={"type": "emby_sync", "actor_id": actor_id})
        assert single.status_code == 202
        assert single.json()["title"] == "Taro"

        # 全量同步没有单一主体, 标题留空而不是取某个演员
        bulk = await client.post("tasks", json={"type": "emby_sync"})
        assert bulk.status_code == 202
        assert bulk.json()["title"] is None


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


class TestTaskResult:
    """GET /tasks/{id}: 结果按任务类型判别; 列表与子任务不带重字段."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_detail_result_is_typed(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """结果按类型判别, 原样回读; 整张表在同一个 lifespan 内跑完."""
        cases: list[tuple[TaskType, dict[str, object]]] = [
            (
                TaskType.SCRAPE,
                {
                    "type": "scrape",
                    "metadata_id": 17,
                    "field_sources": {"title": "dmm"},
                    "failed_sites": ["javdb"],
                    "outcomes": [
                        {
                            "site": "dmm",
                            "outcome": "ok",
                            "reason": None,
                            "http_status": None,
                            "detail": None,
                            "detail_truncated": False,
                        },
                        {
                            "site": "javdb",
                            "outcome": "failed",
                            "reason": "not_found",
                            "http_status": 404,
                            "detail": None,
                            "detail_truncated": False,
                        },
                    ],
                },
            ),
            (
                TaskType.ORGANIZE,
                {
                    "type": "organize",
                    "organized": 1,
                    "skipped": 0,
                    "conflicted": 1,
                    "failed": 0,
                    "pruned_dirs": 0,
                    "conflicts": [
                        {
                            "path": "/lib/incoming/NSFS-039.mp4",
                            "target": "/lib/Studio/NSFS-039/NSFS-039.mp4",
                            "reason": "target_exists",
                        }
                    ],
                },
            ),
            (
                TaskType.SCRAPE,
                {
                    "type": "scrape",
                    "metadata_id": None,
                    "field_sources": {},
                    "failed_sites": ["javdb"],
                    "outcomes": [],
                },
            ),
            (TaskType.REFRESH, {"type": "refresh", "added": 2, "removed": 0, "scrape": 1}),
            (
                TaskType.ACTOR_SCRAPE,
                {
                    "type": "actor_scrape",
                    "actor_id": 9,
                    "field_sources": {},
                    "failed_sites": [],
                    "image_count": 1,
                    "outcomes": [],
                },
            ),
        ]

        for task_type, result in cases:
            task = await repo.create_task(task_type=task_type, payload={})
            assert task.id is not None
            await repo.complete_task(task.id, result=result)

            body = (await client.get(f"tasks/{task.id}")).json()
            assert body["result"] == result
            assert body["result"]["type"] == task_type

    @pytest.mark.asyncio(loop_scope="function")
    async def test_detail_result_unreadable_is_empty(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """旧行与坏数据读不出来时置空, 端点不失败; 整张表在同一个 lifespan 内跑完."""
        stored_cases: list[dict[str, object] | None] = [
            None,
            {"metadata_id": 17, "field_sources": {}, "failed_sites": []},
            {
                "type": "scrape",
                "metadata_id": 17,
                "field_sources": {},
                "failed_sites": [],
                "outcomes": [{"site": "dmm", "outcome": "nope"}],
            },
            {"type": "scrape", "metadata_id": 17},
            {"type": "nope", "metadata_id": 17, "field_sources": {}, "failed_sites": []},
        ]

        for stored in stored_cases:
            task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-497"})
            assert task.id is not None
            await repo.complete_task(task.id, result=stored)

            resp = await client.get(f"tasks/{task.id}")
            assert resp.status_code == 200
            assert resp.json()["result"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failed_task_keeps_result_payload(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """失败任务的站点明细照样能读: 刮削失败时界面靠它解释原因."""
        task = await repo.create_task(task_type=TaskType.SCRAPE, payload={"number": "SSIS-497"})
        assert task.id is not None
        await repo.claim_next_task()
        await repo.fail_task(
            task.id,
            error="未找到 SSIS-497 的元数据",
            result={
                "type": "scrape",
                "metadata_id": None,
                "field_sources": {},
                "failed_sites": ["javdb"],
                "outcomes": [
                    {
                        "site": "javdb",
                        "outcome": "failed",
                        "reason": "not_found",
                        "http_status": 404,
                        "detail": None,
                        "detail_truncated": False,
                    }
                ],
            },
        )

        body = (await client.get(f"tasks/{task.id}")).json()
        assert body["status"] == "failed"
        assert body["error"] == "未找到 SSIS-497 的元数据"
        assert body["result"]["type"] == "scrape"
        assert [row["site"] for row in body["result"]["outcomes"]] == ["javdb"]

    def test_result_union_covers_every_task_type(self):
        """判别联合与结果模型必须覆盖全部任务类型: 漏一个, 该类型的详情会静默置空."""
        members = get_args(get_args(TaskResultPayload)[0])
        assert members, "联合成员为空"
        labels = {get_args(member.model_fields["type"].annotation)[0] for member in members}
        declared = {
            get_args(obj.model_fields["type"].annotation)[0]
            for obj in vars(handler_models).values()
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__name__.endswith("Result")
        }

        assert declared == set(TaskType)
        assert labels == declared

    @pytest.mark.asyncio(loop_scope="function")
    async def test_detail_result_type_mismatch_is_empty(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """结果成员的类型与任务类型不一致时置空."""
        task = await repo.create_task(task_type=TaskType.ORGANIZE, payload={"library_id": 1})
        assert task.id is not None
        await repo.complete_task(task.id, result={"type": "refresh", "added": 1, "removed": 0, "scrape": 0})

        body = (await client.get(f"tasks/{task.id}")).json()
        assert body["result"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_omits_payload_and_result(self, client: AsyncClient, repo: Repository, stop_worker: None):
        """列表不带 payload 与 result, 展开时再取详情."""
        task = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 3})
        assert task.id is not None
        await repo.complete_task(task.id, result={"type": "refresh", "added": 1, "removed": 0, "scrape": 0})

        item = (await client.get("tasks")).json()["items"][0]
        assert "payload" not in item
        assert "result" not in item

        detail = (await client.get(f"tasks/{task.id}")).json()
        assert detail["payload"] == task.payload
        assert detail["result"]["type"] == "refresh"

        # 子任务列表同样只给精简形状.
        parent = await repo.create_task(task_type=TaskType.REFRESH, payload={"library_id": 3})
        assert parent.id is not None
        claimed = await repo.claim_next_task()
        assert claimed is not None and claimed.id == parent.id
        await repo.complete_task_with_followups(
            parent.id,
            result={"type": "refresh", "added": 0, "removed": 0, "scrape": 0},
            followups=[("scrape:1", TaskType.SCRAPE, {"number": "MIDV-123"}, 0)],
        )

        child = (await client.get(f"tasks/{parent.id}/children")).json()["items"][0]
        assert "payload" not in child
        assert "result" not in child


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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_scope_path_lands_on_real_path(self, client: AsyncClient, repo: Repository, safe_path: Path):
        """范围路径与库路径同一形式: 文件选择器给的是真实路径, 别名写法必须先解析为真实路径再入队."""
        real = safe_path / "movies"
        (real / "sub").mkdir(parents=True)
        alias = safe_path / "alias"
        alias.symlink_to(real, target_is_directory=True)
        lib = await repo.create_library(name="a", path=str(real))

        submitted = await client.post(
            "tasks", json={"type": "scan_invalid", "library_id": lib.id, "path": str(alias / "sub")}
        )

        assert submitted.status_code == 202
        assert submitted.json()["payload"]["path"] == str(real / "sub")
