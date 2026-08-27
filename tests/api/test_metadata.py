"""/metadata 端点测试"""

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from PIL import Image

from amane.db.models import MediaFileStatus

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


class TestListMetadata:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_search_pagination(self, client: AsyncClient, repo: Repository):
        empty = await client.get("metadata")
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0

        await repo.upsert_metadata(number="ABC-001", title="Alpha")
        await repo.upsert_metadata(number="XYZ-999", title="Beta")
        listed = await client.get("metadata")
        assert listed.json()["total"] == 2
        search = await client.get("metadata?search=Alpha")
        assert search.json()["total"] == 1

        for i in range(5):
            await repo.upsert_metadata(number=f"TEST-{i:03d}", title=f"Title {i}")
        page = await client.get("metadata?limit=2&offset=1")
        assert len(page.json()["items"]) == 2
        assert page.json()["total"] == 7


class TestGetMetadata:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_with_files(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-001", title="Test")
        assert meta.id is not None

        media = await repo.create_media_file(library_id=1, path="/video/ABC-001.mp4", number="ABC-001")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        resp = await client.get(f"metadata/{meta.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"]["title"] == "Test"
        assert len(data["files"]) == 1
        assert (await client.get("metadata/9999")).status_code == 404


class TestUpdateMetadata:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_release_normalizes(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="ABC-REL-1")
        assert meta.id is not None

        ok = await client.patch(f"metadata/{meta.id}", json={"release": "2020-01-15T00:00:00Z"})
        assert ok.status_code == 200
        assert ok.json()["release"] == "2020-01-15"

        bad = await client.patch(f"metadata/{meta.id}", json={"release": "not-a-date"})
        assert bad.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_url_lists_and_scores(self, client: AsyncClient, repo: Repository):
        """编辑原始可写字段 (poster_urls/scores/external_ids) 并能从 response 读回."""
        meta = await repo.upsert_metadata(number="ABC-001")
        assert meta.id is not None
        resp = await client.patch(
            f"metadata/{meta.id}",
            json={
                "poster_urls": ["https://a/p.jpg", "https://b/p.jpg"],
                "scores": {"javdb": 4.5},
                "external_ids": {"javdb": "abc"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["poster_urls"] == ["https://a/p.jpg", "https://b/p.jpg"]
        assert data["scores"] == {"javdb": 4.5}
        assert data["external_ids"] == {"javdb": "abc"}
        # 计算属性同步反映
        assert data["poster_url"] == "https://a/p.jpg"
        assert data["score"] == 4.5

        assert (await client.patch("metadata/9999", json={"title": "X"})).status_code == 404
        assert (await client.patch(f"metadata/{meta.id}", json={})).status_code == 422
        extra = await client.patch(f"metadata/{meta.id}", json={"title": "New", "unknown_field": "ignored"})
        assert extra.status_code == 200
        assert extra.json()["title"] == "New"
        for bad_payload in ({"runtime": "not_an_int"}, {"actors": "not_a_list"}):
            assert (await client.patch(f"metadata/{meta.id}", json=bad_payload)).status_code == 422


class TestMetadataSchema:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_schema_includes_editable_fields(self, client: AsyncClient):
        resp = await client.get("metadata/schema")
        assert resp.status_code == 200
        props = resp.json()["properties"]
        # 标量 + 列表 + dict 可写字段都在
        for key in ("title", "actors", "poster_urls", "scores", "external_ids", "source_urls"):
            assert key in props


class TestDeleteMetadata:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_nullifies_media_file_fk(self, client: AsyncClient, repo: Repository):
        """删除 Metadata 经 API 应应用层级联清空 MediaFile.metadata_id, 状态回 PENDING."""
        meta = await repo.upsert_metadata(number="ABC-001", title="Test")
        assert meta.id is not None
        media = await repo.create_media_file(library_id=1, path="/video/ABC-001.mp4", number="ABC-001")
        assert media.id is not None
        await repo.update_media_file(media.id, status=MediaFileStatus.SCRAPED, metadata_id=meta.id)

        resp = await client.delete(f"metadata/{meta.id}")
        assert resp.status_code == 204

        updated = await repo.get_media_file(media.id)
        assert updated is not None
        assert updated.metadata_id is None
        assert updated.status == MediaFileStatus.PENDING
        assert (await client.delete("metadata/9999")).status_code == 404


class TestMergeMetadata:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_scalar_field(self, client: AsyncClient, repo: Repository):
        """从 raw 中选择特定来源的字段值."""
        meta = await repo.upsert_metadata(
            number="ABC-001",
            title="javdb title",
            field_sources={"title": "javdb"},
            raw={"javdb": {"title": "javdb title"}, "dmm": {"title": "dmm title"}},
        )
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "dmm"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "dmm title"
        assert data["field_sources"]["title"] == "dmm"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_score_to_scores(self, client: AsyncClient, repo: Repository):
        """score 字段映射到 scores dict."""
        meta = await repo.upsert_metadata(number="ABC-002", raw={"javdb": {"score": 85.0}})
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"score": "javdb"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scores"]["javdb"] == 85.0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_invalid_source(self, client: AsyncClient, repo: Repository):
        """非法 source 返回 422."""
        meta = await repo.upsert_metadata(number="ABC-003")
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "nonexistent"}})
        assert resp.status_code == 422
        assert "nonexistent" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_noop_when_value_none(self, client: AsyncClient, repo: Repository):
        """raw 中字段值为 null 时静默跳过."""
        meta = await repo.upsert_metadata(
            number="ABC-004", field_sources={"title": "javdb"}, raw={"javdb": {"title": None}}
        )
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "javdb"}})
        assert resp.status_code == 400  # no valid selections

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_empty_selections(self, client: AsyncClient, repo: Repository):
        """空 selections 返回 400."""
        meta = await repo.upsert_metadata(number="ABC-005")
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {}})
        assert resp.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_not_found(self, client: AsyncClient):
        """不存在的 metadata 返回 404."""
        resp = await client.post("metadata/9999/merge", json={"selections": {"title": "javdb"}})
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_merge_preserves_existing_field_sources(self, client: AsyncClient, repo: Repository):
        """合并时保留已有的 field_sources 其他条目."""
        meta = await repo.upsert_metadata(
            number="ABC-006",
            title="javdb title",
            studio="javdb studio",
            field_sources={"title": "javdb", "studio": "javdb"},
            raw={"javdb": {"title": "javdb title", "studio": "javdb studio"}, "dmm": {"title": "dmm title"}},
        )
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": {"title": "dmm"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "dmm title"
        assert data["field_sources"]["title"] == "dmm"
        assert data["field_sources"]["studio"] == "javdb"  # 未被覆盖

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("selections", "check"),
        [
            (
                {"poster_urls": "dmm"},
                lambda data: data["poster_urls"] == ["https://dmm/p.jpg"],
            ),
            (
                {"extrafanart": "javdb"},
                lambda data: data["extrafanart_urls"] == {"javdb": ["https://j/e1.jpg", "https://j/e2.jpg"]},
            ),
            (
                {"thumb_urls": "javdb", "extrafanart": "dmm"},
                lambda data: (
                    data["thumb_urls"] == ["https://j/t.jpg"]
                    and data["extrafanart_urls"] == {"dmm": ["https://d/e.jpg"]}
                ),
            ),
        ],
        ids=["poster_urls", "extrafanart", "thumb_and_extrafanart"],
    )
    async def test_merge_image_fields(
        self,
        client: AsyncClient,
        repo: Repository,
        selections: dict[str, str],
        check,
    ):
        """图片字段按源整组替换: poster/thumb 写 list, extrafanart 写单站 dict."""
        meta = await repo.upsert_metadata(
            number="ABC-IMG-001",
            poster_urls=["https://old/p.jpg"],
            thumb_urls=["https://old/t.jpg"],
            extrafanart_urls={"old": ["https://old/e.jpg"]},
            raw={
                "javdb": {
                    "poster_urls": ["https://j/p.jpg"],
                    "thumb_urls": ["https://j/t.jpg"],
                    "extrafanart": ["https://j/e1.jpg", "https://j/e2.jpg"],
                },
                "dmm": {
                    "poster_urls": ["https://dmm/p.jpg"],
                    "thumb_urls": ["https://dmm/t.jpg"],
                    "extrafanart": ["https://d/e.jpg"],
                },
            },
        )
        assert meta.id is not None

        resp = await client.post(f"metadata/{meta.id}/merge", json={"selections": selections})
        assert resp.status_code == 200
        assert check(resp.json())


class TestCropPoster:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_crop_success(self, client: AsyncClient, repo: Repository, app: FastAPI):
        meta = await repo.upsert_metadata(
            number="CROP-001", thumb_urls=["https://example.com/t.jpg"], poster_urls=["https://example.com/old-p.jpg"]
        )
        assert meta.id is not None

        async def fake_download(url: str, dest: Path, **kwargs) -> bool:
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
        assert data["poster_url"] == data["poster_urls"][0]
        assert data["thumb_urls"] == ["https://example.com/t.jpg"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_crop_idempotent(self, client: AsyncClient, repo: Repository, app: FastAPI):
        meta = await repo.upsert_metadata(number="CROP-002", thumb_urls=["https://example.com/t2.jpg"])
        assert meta.id is not None

        async def fake_download(url: str, dest: Path, **kwargs) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "green").save(dest)
            return True

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        body = {"left": 100, "top": 10, "right": 400, "bottom": 500}

        r1 = await client.post(f"metadata/{meta.id}/crop-poster", json=body)
        r2 = await client.post(f"metadata/{meta.id}/crop-poster", json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["poster_urls"] == r2.json()["poster_urls"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_thumb(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="CROP-003", poster_urls=["https://example.com/p.jpg"])
        assert meta.id is not None
        resp = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10}
        )
        assert resp.status_code == 400
        assert "封面" in resp.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_box_out_of_bounds(self, client: AsyncClient, repo: Repository, app: FastAPI):
        meta = await repo.upsert_metadata(number="CROP-004", thumb_urls=["https://example.com/t3.jpg"])
        assert meta.id is not None

        async def fake_download(url: str, dest: Path, **kwargs) -> bool:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 538), "blue").save(dest)
            return True

        app.state.runtime.web_client.download = fake_download  # type: ignore[method-assign]
        resp = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 0, "top": 0, "right": 900, "bottom": 538}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_box_payload(self, client: AsyncClient, repo: Repository):
        meta = await repo.upsert_metadata(number="CROP-005", thumb_urls=["https://example.com/t4.jpg"])
        assert meta.id is not None
        resp = await client.post(
            f"metadata/{meta.id}/crop-poster", json={"left": 100, "top": 0, "right": 50, "bottom": 100}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_not_found(self, client: AsyncClient):
        resp = await client.post("metadata/99999/crop-poster", json={"left": 0, "top": 0, "right": 10, "bottom": 10})
        assert resp.status_code == 404
