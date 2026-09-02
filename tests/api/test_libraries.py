"""/libraries 端点测试"""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType
from amane.organize import VIDEO_TEMPLATE_DEFAULT

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestLibraries:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_crud_and_defaults(self, client: AsyncClient, repo: Repository, safe_path: Path):
        empty = await client.get("libraries")
        assert empty.status_code == 200
        assert empty.json()["items"] == []

        target = safe_path / "incoming"
        target.mkdir()
        created = await client.post(
            "libraries",
            json={"path": str(target), "patterns": ["*.mp4", "*.mkv"], "scan": False},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["path"] == str(target)
        assert body["automation"] == "scrape"
        assert body["name"] == "incoming"
        assert body["move_mode"] == "move"
        assert body["link_template"] is None
        assert body["link_mode"] == "strm"
        assert body["strm_content_template"] is None
        assert body["write_nfo"] is True
        assert set(body["copy_resources"]) == {"thumb", "poster", "extrafanart", "trailer"}
        assert body["trailer_pattern"] == "(?i)trailer"
        assert body["blacklist_patterns"] == []
        assert body["min_file_size"] == 0
        assert body["subtitle_extensions"] == [".srt", ".ass", ".ssa", ".vtt", ".sub"]
        assert body["video_template"] == VIDEO_TEMPLATE_DEFAULT
        assert body["thumb_template"] is None
        assert body["poster_template"] is None
        lib_id = body["id"]

        listed = await client.get("libraries")
        assert len(listed.json()["items"]) == 1
        one = await client.get(f"libraries/{lib_id}")
        assert one.status_code == 200
        assert one.json()["name"] == "incoming"
        assert (await client.get("libraries/9999")).status_code == 404

        full_dir = safe_path / "full"
        full_dir.mkdir()
        full = await client.post(
            "libraries",
            json={
                "name": "Full Library",
                "path": str(full_dir),
                "automation": "none",
                "recursive": False,
                "patterns": ["*.mp4"],
                "move_mode": "hardlink",
                "video_template": "/out/{number}/{number}.mp4",
                "poster_template": "/out/{number}/poster.jpg",
                "thumb_template": "/out/{number}/thumb.jpg",
                "fanart_template": "/out/{number}/fanart.jpg",
                "blacklist_patterns": ["广告", "(?i)ads[0-9]+"],
                "min_file_size": 10485760,
                "scan": False,
            },
        )
        assert full.status_code == 201
        fbody = full.json()
        assert fbody["name"] == "Full Library"
        assert fbody["move_mode"] == "hardlink"
        assert fbody["video_template"] == "/out/{number}/{number}.mp4"
        assert fbody["blacklist_patterns"] == ["广告", "(?i)ads[0-9]+"]
        assert fbody["min_file_size"] == 10485760

        patched = await client.patch(
            f"libraries/{lib_id}",
            json={
                "move_mode": "symlink",
                "write_nfo": False,
                "copy_resources": ["thumb"],
                "trailer_pattern": "预告",
                "blacklist_patterns": ["广告", "预览"],
                "min_file_size": 0,
                "subtitle_extensions": ["vtt"],
                "link_template": str(safe_path / "emby" / "{number}" / "{number}.{ext}"),
                "link_mode": "symlink",
                "strm_content_template": "/{video_relpath}",
            },
        )
        assert patched.status_code == 200
        pbody = patched.json()
        assert pbody["move_mode"] == "symlink"
        assert pbody["write_nfo"] is False
        assert pbody["copy_resources"] == ["thumb"]
        assert pbody["trailer_pattern"] == "预告"
        assert pbody["blacklist_patterns"] == ["广告", "预览"]
        assert pbody["min_file_size"] == 0
        assert pbody["subtitle_extensions"] == [".vtt"]
        assert pbody["link_mode"] == "symlink"
        assert pbody["link_template"] == str(safe_path / "emby" / "{number}" / "{number}.{ext}")
        assert pbody["strm_content_template"] == "/{video_relpath}"

        cleared_strm = await client.patch(f"libraries/{lib_id}", json={"strm_content_template": "  "})
        assert cleared_strm.status_code == 200
        assert cleared_strm.json()["strm_content_template"] is None

        cleared = await client.patch(f"libraries/{lib_id}", json={"patterns": []})
        assert cleared.status_code == 200
        assert cleared.json()["patterns"] == []

        empty_patch = await client.patch(f"libraries/{lib_id}", json={})
        assert empty_patch.status_code == 422
        assert (await client.patch(f"libraries/{lib_id}", json={"patterns": None})).status_code == 422
        assert (await client.patch("libraries/9999", json={"automation": "none"})).status_code == 404

        await repo.create_media_file(library_id=lib_id, path=str(target / "a.mp4"))
        await repo.create_media_file(library_id=lib_id, path=str(target / "b.mp4"))
        deleted = await client.delete(f"libraries/{lib_id}")
        assert deleted.status_code == 204
        assert await repo.count_media_files(library_id=lib_id) == 0
        assert await repo.get_library(lib_id) is None
        assert (await client.delete("libraries/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_rejects_illegal_payloads(self, client: AsyncClient, safe_path: Path):
        missing = await client.post("libraries", json={"path": str(safe_path / "does-not-exist")})
        assert missing.status_code == 404
        outside = await client.post("libraries", json={"path": "/etc"})
        assert outside.status_code == 403
        as_file = safe_path / "a-file.txt"
        as_file.write_text("hello")
        assert (await client.post("libraries", json={"path": str(as_file)})).status_code == 400

        target = safe_path / "bad"
        target.mkdir()
        base = {"path": str(target), "scan": False}
        assert (await client.post("libraries", json={**base, "move_mode": "foobar"})).status_code == 422
        assert (await client.post("libraries", json={**base, "link_mode": "foobar"})).status_code == 422
        assert (await client.post("libraries", json={**base, "trailer_pattern": "[unclosed"})).status_code == 422
        assert (
            await client.post("libraries", json={**base, "blacklist_patterns": ["广告", "(ads"]})
        ).status_code == 422
        assert (await client.post("libraries", json={**base, "min_file_size": -1})).status_code == 422
        assert (
            await client.post("libraries", json={**base, "subtitle_extensions": [".srt", "bad ext"]})
        ).status_code == 422
        assert (
            await client.post("libraries", json={**base, "video_template": "{number}[-CD{cd?}.{ext}"})
        ).status_code == 422
        assert (await client.post("libraries", json={**base, "video_template": "{number}].{ext}"})).status_code == 422
        assert (
            await client.post("libraries", json={**base, "video_template": "{mosaic?|uncencored=U}"})
        ).status_code == 422
        assert (
            await client.post("libraries", json={**base, "strm_content_template": "/{video_relpath}\n"})
        ).status_code == 422
        assert (await client.post("libraries", json={**base, "strm_content_template": "{number}"})).status_code == 422

        ok_empty_trailer = await client.post("libraries", json={**base, "trailer_pattern": ""})
        assert ok_empty_trailer.status_code == 201
        assert ok_empty_trailer.json()["trailer_pattern"] == ""

        sub_dir = target / "sub-norm"
        sub_dir.mkdir()
        norm = await client.post(
            "libraries",
            json={"path": str(sub_dir), "subtitle_extensions": ["SRT", ".ass", "srt"], "scan": False},
        )
        assert norm.status_code == 201
        assert norm.json()["subtitle_extensions"] == [".srt", ".ass"]

        off_dir = target / "sub-off"
        off_dir.mkdir()
        off = await client.post("libraries", json={"path": str(off_dir), "subtitle_extensions": [], "scan": False})
        assert off.status_code == 201
        assert off.json()["subtitle_extensions"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_allow_all_creates_library_outside_files_dir(self, allow_all_client: AsyncClient, tmp_path: Path):
        target = tmp_path / "nas-share"
        target.mkdir()
        resp = await allow_all_client.post("libraries", json={"path": str(target), "scan": False})
        assert resp.status_code == 201, resp.text
        assert Path(resp.json()["path"]).resolve() == target.resolve()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_path_and_invalid_fields(self, client: AsyncClient, repo: Repository, safe_path: Path):
        lib = await repo.create_library(name="t", path=str(safe_path))
        assert (await client.patch(f"libraries/{lib.id}", json={"path": str(safe_path / "missing")})).status_code == 404

        dangling = await repo.create_library(name="d", path="/nonexistent")
        skipped = await client.patch(f"libraries/{dangling.id}", json={"automation": "none"})
        assert skipped.status_code == 200
        assert skipped.json()["automation"] == "none"

        assert (await client.patch(f"libraries/{lib.id}", json={"blacklist_patterns": ["(ads"]})).status_code == 422
        assert (await client.patch(f"libraries/{lib.id}", json={"min_file_size": -1})).status_code == 422
        assert (
            await client.patch(f"libraries/{lib.id}", json={"video_template": "{number}[-CD{cd?}"})
        ).status_code == 422
        assert (await client.patch(f"libraries/{lib.id}", json={"trailer_pattern": "[unclosed"})).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_scan_flag(self, client: AsyncClient, repo: Repository, safe_path: Path):
        scanned = safe_path / "auto-scan"
        scanned.mkdir()
        resp = await client.post("libraries", json={"path": str(scanned), "scan": True})
        assert resp.status_code == 201
        lib_id = resp.json()["id"]
        tasks = await repo.list_tasks(task_types=[TaskType.REFRESH])
        scan_tasks = [t for t in tasks if t.payload.get("library_id") == lib_id]
        assert len(scan_tasks) == 1
        assert scan_tasks[0].payload["scan"] == ["add"]
        assert scan_tasks[0].payload["scrape"] == []
        assert scan_tasks[0].payload["path"] == str(scanned)

        quiet = safe_path / "no-scan"
        quiet.mkdir()
        quiet_resp = await client.post("libraries", json={"path": str(quiet), "scan": False})
        assert quiet_resp.status_code == 201
        quiet_id = quiet_resp.json()["id"]
        still = [
            t for t in await repo.list_tasks(task_types=[TaskType.REFRESH]) if t.payload.get("library_id") == quiet_id
        ]
        assert still == []
