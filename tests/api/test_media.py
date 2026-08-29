"""/media 端点测试"""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from amane.db.models import MediaFileStatus

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    """FK 约束要求归属库存在; 这些测试以 library_id=1 创建 MediaFile."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


class TestListMedia:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_filter_search(self, client: AsyncClient, repo: Repository):
        empty = await client.get("media")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0

        await repo.create_media_file(library_id=1, path="/video/a.mp4", number="ABC-001")
        await repo.create_media_file(library_id=1, path="/other/b.mkv", number="DEF-002")
        listed = await client.get("media")
        assert listed.json()["total"] == 2

        m1 = await repo.create_media_file(library_id=1, path="/a.mp4")
        await repo.create_media_file(library_id=1, path="/b.mp4")
        assert m1.id is not None
        await repo.update_media_file(m1.id, status=MediaFileStatus.SCRAPED)
        pending = await client.get("media?status=pending")
        assert pending.json()["total"] == 3

        for i in range(5):
            await repo.create_media_file(library_id=1, path=f"/video/{i}.mp4")
        page = await client.get("media?limit=2&offset=0")
        assert len(page.json()["items"]) == 2
        assert page.json()["total"] == 9

        by_path = await client.get("media?search=other")
        assert by_path.json()["total"] == 1
        assert by_path.json()["items"][0]["path"] == "/other/b.mkv"
        by_num = await client.get("media?search=ABC")
        assert by_num.json()["total"] == 1
        assert by_num.json()["items"][0]["number"] == "ABC-001"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_phase_filters_and_fields(self, client: AsyncClient, repo: Repository):
        await repo.create_media_file(library_id=1, path="/video/MIDV-001-C.mp4")
        await repo.create_media_file(library_id=1, path="/video/HEYZO-1234.mp4")
        listed = await client.get("media?search=MIDV-001")
        item = listed.json()["items"][0]
        assert item["has_subtitle"] is True
        assert item["content_type"] == "censored"
        heyzo = await client.get("media?uncensored=true")
        assert heyzo.json()["total"] == 1
        assert heyzo.json()["items"][0]["content_type"] == "uncensored"
        assert heyzo.json()["items"][0]["mosaic"] is None
        bad = await client.get("media?definition=not-a-def")
        assert bad.status_code == 422


class TestGetMedia:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_update_delete(self, client: AsyncClient, repo: Repository):
        media = await repo.create_media_file(library_id=1, path="/video/x.mp4", number="XYZ-001")
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
