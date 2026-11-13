"""/tasks/batch/submit HTTP 接线: 逐项解析、计数口径与整批入队."""

from typing import TYPE_CHECKING

import pytest

from amane.db.models import FacetKind, TaskType

if TYPE_CHECKING:
    from pathlib import Path

    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestTaskBatchSubmit:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_scrape_by_media_id_and_number(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None
    ) -> None:
        """media_id 按挂载路径推断并保留文件 id; number 直接入队并透传给定字段."""
        media = await seed_library.create_media_file(library_id=1, path="/video/ABC-123-C.mp4")
        assert media.id is not None

        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "scrape", "media_id": media.id},
                    {"type": "scrape", "number": "DEF-456", "content_type": "uncensored", "use_cache": []},
                ]
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert (body["submitted"], body["missing"], body["skipped"]) == (2, 0, 0)

        from_media = await seed_library.get_task(body["task_ids"][0])
        from_number = await seed_library.get_task(body["task_ids"][1])
        assert from_media is not None and from_number is not None
        assert from_media.type == TaskType.SCRAPE
        assert from_media.payload["number"] == "ABC-123"
        assert from_media.payload["media_file_id"] == media.id
        assert from_media.payload["content_type"] == "censored"
        assert from_number.payload["number"] == "DEF-456"
        assert from_number.payload["content_type"] == "uncensored"
        assert from_number.payload["use_cache"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_and_in_request_duplicates(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None
    ) -> None:
        """同番号无文件去重; 有文件时按文件各建一条 (逐文件结算不能合并)."""
        first = await seed_library.create_media_file(library_id=1, path="/video/GHI-789-C.mp4")
        second = await seed_library.create_media_file(library_id=1, path="/video/backup/GHI-789-C.mp4")
        assert first.id is not None and second.id is not None

        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "scrape", "number": "dup-1"},
                    {"type": "scrape", "number": "DUP-1"},
                    {"type": "scrape", "media_id": first.id, "number": "SAME-1"},
                    {"type": "scrape", "media_id": second.id, "number": "SAME-1"},
                    {"type": "scrape", "media_id": 999_999},
                    {"type": "actor_scrape", "actor_id": 999_999},
                ]
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert (body["submitted"], body["missing"], body["skipped"]) == (3, 2, 1)
        assert len(body["task_ids"]) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_actor_scrape_reuses_active_task(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None
    ) -> None:
        await seed_library.upsert_metadata(number="ACT-BATCH-SCRAPE", actors=["BatchScrape"])
        actors, _ = await seed_library.list_facets(FacetKind.ACTOR)
        actor_id = next(a.id for a in actors if a.name == "BatchScrape")
        assert actor_id is not None
        existing = await seed_library.create_task(task_type=TaskType.ACTOR_SCRAPE, payload={"actor_id": actor_id})
        assert existing.id is not None

        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "actor_scrape", "actor_id": actor_id},
                    {"type": "actor_scrape", "actor_id": actor_id},
                ]
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert (body["submitted"], body["skipped"]) == (1, 1)
        assert body["task_ids"] == [existing.id]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_url_scrapes_dedupe_by_url(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None
    ) -> None:
        """网址刮削没有番号: 同一网址去重, 不同网址各自入队."""
        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "scrape", "url": "https://javdb.com/v/aaa"},
                    {"type": "scrape", "url": "https://javdb.com/v/bbb"},
                    {"type": "scrape", "url": "https://javdb.com/v/aaa"},
                ]
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert (body["submitted"], body["skipped"]) == (2, 1)
        first = await seed_library.get_task(body["task_ids"][0])
        assert first is not None
        assert first.payload["url"] == "https://javdb.com/v/aaa"
        assert first.payload["number"] == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_other_types_resolve_per_item(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None, safe_path: Path
    ) -> None:
        """非刮削类型逐条走单条解析: 库默认值照常落到 payload, 库缺失计入 missing."""
        lib = await seed_library.create_library(name="batch", path=str(safe_path))
        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "refresh", "library_id": lib.id},
                    {"type": "refresh", "library_id": 999_999},
                ]
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert (body["submitted"], body["missing"]) == (1, 1)
        task = await seed_library.get_task(body["task_ids"][0])
        assert task is not None
        assert task.type == TaskType.REFRESH
        assert task.payload["path"] == str(safe_path)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_parameter_error_aborts_whole_batch(
        self, client: AsyncClient, seed_library: Repository, stop_worker: None, safe_path: Path
    ) -> None:
        """参数互斥属请求级错误: 整批不入队, 不降级为 missing."""
        lib = await seed_library.create_library(name="batch", path=str(safe_path))
        resp = await client.post(
            "tasks/batch/submit",
            json={
                "submissions": [
                    {"type": "scrape", "number": "OK-1"},
                    {"type": "organize", "library_id": lib.id, "path": str(safe_path), "media_file_ids": [1]},
                ]
            },
        )
        assert resp.status_code == 422
        assert (await client.get("tasks")).json()["total"] == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_invalid_bodies(self, client: AsyncClient) -> None:
        """空数组 / 未登记的 type / 缺来源 / 非法 use_cache / 超上限都是 422."""
        cases: list[list[dict[str, object]]] = [
            [],
            [{"type": "bogus"}],
            [{"type": "scrape"}],
            [{"type": "scrape", "number": "X", "use_cache": ["bogus"]}],
            [{"type": "cleanup"}] * 1001,
        ]
        for submissions in cases:
            resp = await client.post("tasks/batch/submit", json={"submissions": submissions})
            assert resp.status_code == 422, submissions
