"""/media HTTP 接线: 状态码、JSON 形状、422. 列表筛选见 tests/db/test_repository.py."""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from amane.config import ParsingConfig, build_number_rules
from amane.parsing import EMPTY_NUMBER_RULES, ContentType

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    """FK 约束要求归属库存在; 这些测试以 library_id=1 创建 MediaFile."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


class TestMediaHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_get_update_delete(self, client: AsyncClient, repo: Repository):
        empty = await client.get("media")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert (await client.get("media?definition=not-a-def")).status_code == 422

        media = await repo.create_media_file(
            library_id=1, path="/video/MIDV-001-C.mp4", number="XYZ-001", rules=EMPTY_NUMBER_RULES
        )
        listed = await client.get("media?search=MIDV-001")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert item["has_subtitle"] is True
        assert item["content_type"] == "censored"

        got = await client.get(f"media/{media.id}")
        assert got.status_code == 200
        assert got.json()["number"] == "XYZ-001"
        assert (await client.get("media/9999")).status_code == 404

        patched = await client.patch(f"media/{media.id}", json={"number": "NEW-001", "status": "scraped"})
        assert patched.status_code == 200
        assert patched.json()["number"] == "NEW-001"
        assert patched.json()["status"] == "scraped"
        meta = await repo.upsert_metadata(number="ABC-123")
        moved = await client.patch(f"media/{media.id}", json={"path": "/new/location/x.mp4", "metadata_id": meta.id})
        assert moved.json()["path"] == "/new/location/x.mp4"
        assert moved.json()["metadata_id"] == meta.id
        extra = await client.patch(f"media/{media.id}", json={"number": "NEW", "unknown_field": "should_be_ignored"})
        assert extra.status_code == 200
        assert extra.json()["number"] == "NEW"
        assert (await client.patch("media/9999", json={"number": "X"})).status_code == 404
        assert (await client.patch(f"media/{media.id}", json={})).status_code == 422
        for bad in ({"status": "invalid_status"}, {"number": 12345}):
            assert (await client.patch(f"media/{media.id}", json=bad)).status_code == 422

        deleted = await client.delete(f"media/{media.id}")
        assert deleted.status_code == 204
        assert (await client.delete("media/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_patch_path_recomputes_phase_with_runtime_rules(self, client: AsyncClient, app: FastAPI, repo):
        """PATCH 带 path 时按运行时规则重算相位 (走 rewrite_media_path 分支)."""
        media = await repo.create_media_file(library_id=1, path="/video/plain.mp4", rules=EMPTY_NUMBER_RULES)
        app.state.runtime.number_rules = build_number_rules(
            ParsingConfig(prefix_types={"MIDV": ContentType.UNCENSORED})
        )

        patched = await client.patch(f"media/{media.id}", json={"path": "/video/MIDV-123.mp4"})

        assert patched.status_code == 200
        assert patched.json()["path"] == "/video/MIDV-123.mp4"
        assert patched.json()["content_type"] == "uncensored"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_patch_without_path_keeps_phase(self, client: AsyncClient, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/MIDV-123.mp4", rules=EMPTY_NUMBER_RULES)

        patched = await client.patch(f"media/{media.id}", json={"number": "MIDV-999"})

        assert patched.status_code == 200
        assert patched.json()["content_type"] == "censored"
