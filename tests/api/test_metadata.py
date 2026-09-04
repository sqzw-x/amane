"""/metadata HTTP 接线: 状态码、JSON 形状、422. 列表筛选/级联删除见 tests/db; 合并规则见 tests/aggregate."""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from PIL import Image

from amane.db.models import MediaFileStatus
from amane.enums import ActorGender

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


@pytest_asyncio.fixture(autouse=True)
async def _seed_library(repo: Repository) -> None:
    """FK 约束要求归属库存在; 这些测试以 library_id=1 创建 MediaFile."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")


class TestMetadataHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_detail_update_delete(self, client: AsyncClient, repo: Repository):
        empty = await client.get("metadata")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert (await client.get("metadata?definition=bogus")).status_code == 422

        meta = await repo.upsert_metadata(
            number="ABC-001",
            title="Test",
            actors=["Mei", "MaleA"],
            actor_genders={"Mei": ActorGender.FEMALE},
        )
        assert meta.id is not None
        media = await repo.create_media_file(library_id=1, path="/video/MIDV-001-C.mp4", number="ABC-001")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        listed = await client.get("metadata?search=ABC")
        assert listed.status_code == 200
        item = listed.json()["items"][0]
        assert item["file_count"] == 1
        assert item["file_phase"]["has_subtitle"] is True

        detail = await client.get(f"metadata/{meta.id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["metadata"]["title"] == "Test"
        assert len(body["files"]) == 1
        assert body["actor_genders"] == {"Mei": "female", "MaleA": "unknown"}
        assert set(body["actor_ids"]) == {"Mei", "MaleA"}
        assert (await client.get("metadata/9999")).status_code == 404

        ok = await client.patch(f"metadata/{meta.id}", json={"release": "2020-01-15T00:00:00Z"})
        assert ok.status_code == 200
        assert ok.json()["release"] == "2020-01-15"
        assert (await client.patch(f"metadata/{meta.id}", json={"release": "not-a-date"})).status_code == 422

        urls = await client.patch(
            f"metadata/{meta.id}",
            json={
                "poster_urls": ["https://a/p.jpg", "https://b/p.jpg"],
                "scores": {"javdb": 4.5},
                "external_ids": {"javdb": "abc"},
            },
        )
        assert urls.status_code == 200
        data = urls.json()
        assert data["poster_urls"] == ["https://a/p.jpg", "https://b/p.jpg"]
        assert data["poster_url"] == "https://a/p.jpg"
        assert data["score"] == 4.5
        assert (await client.patch("metadata/9999", json={"title": "X"})).status_code == 404
        assert (await client.patch(f"metadata/{meta.id}", json={})).status_code == 422
        extra = await client.patch(f"metadata/{meta.id}", json={"title": "New", "unknown_field": "ignored"})
        assert extra.status_code == 200
        assert extra.json()["title"] == "New"
        for bad_payload in ({"runtime": "not_an_int"}, {"actors": "not_a_list"}):
            assert (await client.patch(f"metadata/{meta.id}", json=bad_payload)).status_code == 422

        deleted = await client.delete(f"metadata/{meta.id}")
        assert deleted.status_code == 204
        assert (await client.delete("metadata/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_schema_includes_editable_fields(self, client: AsyncClient):
        resp = await client.get("metadata/schema")
        assert resp.status_code == 200
        props = resp.json()["properties"]
        for key in ("title", "actors", "poster_urls", "scores", "external_ids", "source_urls"):
            assert key in props

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_http(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(
            number="ABC-001",
            title="javdb title",
            field_sources={"title": "javdb"},
            raw={"javdb": {"title": "javdb title"}, "dmm": {"title": "dmm title"}},
        )
        assert meta.id is not None
        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "dmm"}})
        assert resp.status_code == 200
        assert resp.json()["title"] == "dmm title"
        assert resp.json()["field_sources"]["title"] == "dmm"

        none_meta = await repo.upsert_metadata(
            number="ABC-004", field_sources={"title": "javdb"}, raw={"javdb": {"title": None}}
        )
        assert none_meta.id is not None
        assert (
            await client.post(f"metadata/{none_meta.id}/merge", json={"selections": {"title": "javdb"}})
        ).status_code == 400
        assert (await client.post(f"metadata/{meta.id}/merge", json={"selections": {}})).status_code == 400
        assert (await client.post("metadata/9999/merge", json={"selections": {"title": "javdb"}})).status_code == 404
        bad = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "nonexistent"}})
        assert bad.status_code == 422
        assert "nonexistent" in bad.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_crop_poster_http(self, client: AsyncClient, repo: Repository, app: FastAPI):
        meta = await repo.upsert_metadata(
            number="CROP-001", thumb_urls=["https://example.com/t.jpg"], poster_urls=["https://example.com/old-p.jpg"]
        )
        assert meta.id is not None

        async def fake_download(url: str, dest: Path, **kwargs: object) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "blue").save(dest)
            return True

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]

        resp = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 421, "top": 0, "right": 800, "bottom": 538}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["poster_urls"]) == 1
        assert data["poster_urls"][0].startswith("/api/resources/")
        assert data["thumb_urls"] == ["https://example.com/t.jpg"]

        no_thumb = await repo.upsert_metadata(number="CROP-003", poster_urls=["https://example.com/p.jpg"])
        assert no_thumb.id is not None
        missing_thumb = await client.post(
            f"metadata/{no_thumb.id}/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10}
        )
        assert missing_thumb.status_code == 400
        assert "封面" in missing_thumb.json()["detail"]

        inverted = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 100, "top": 0, "right": 50, "bottom": 100}
        )
        assert inverted.status_code == 422
        assert (
            await client.post("metadata/99999/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10})
        ).status_code == 404
