"""/libraries 端点测试"""

from typing import TYPE_CHECKING

import pytest

from amane.db.models import TaskType

if TYPE_CHECKING:
    from pathlib import Path

    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestLibraries:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_empty(self, client: AsyncClient):
        resp = await client.get("libraries")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_list(self, client: AsyncClient, safe_path: Path):
        # tmp_path 已通过 conftest 加入 safe_dirs, 路径校验会通过
        target = safe_path / "incoming"
        target.mkdir()
        resp = await client.post(
            "libraries",
            json={
                "path": str(target),
                "patterns": ["*.mp4", "*.mkv"],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["path"] == str(target)
        assert resp.json()["automation"] == "scrape"
        # name 默认取 basename
        assert resp.json()["name"] == "incoming"
        assert resp.json()["move_mode"] == "move"
        assert resp.json()["write_nfo"] is True
        assert set(resp.json()["copy_resources"]) == {"thumb", "poster", "extrafanart", "trailer"}
        assert resp.json()["trailer_pattern"] == "(?i)trailer"

        resp = await client.get("libraries")
        assert len(resp.json()["items"]) == 1
        lib_id = resp.json()["items"][0]["id"]
        one = await client.get(f"libraries/{lib_id}")
        assert one.status_code == 200
        assert one.json()["name"] == "incoming"
        missing = await client.get("libraries/9999")
        assert missing.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_rejects_illegal_paths(self, client: AsyncClient, safe_path: Path):
        missing = await client.post("libraries", json={"path": str(safe_path / "does-not-exist")})
        assert missing.status_code == 404
        outside = await client.post("libraries", json={"path": "/etc"})
        assert outside.status_code == 403
        target = safe_path / "a-file.txt"
        target.write_text("hello")
        not_dir = await client.post("libraries", json={"path": str(target)})
        assert not_dir.status_code == 400

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_path_validated(self, client: AsyncClient, repo: Repository, safe_path: Path):
        # 直接通过 repo 创建 (绕过 API 校验) 然后通过 API 更新一个不存在的路径
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(f"libraries/{lib.id}", json={"path": str(safe_path / "missing")})
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_other_fields_no_path_check(self, client: AsyncClient, repo: Repository, safe_path: Path):
        # 不更新 path 时不需要路径校验, 即使 lib.path 是无效的也应成功
        lib = await repo.create_library(name="t", path="/nonexistent")
        resp = await client.patch(f"libraries/{lib.id}", json={"automation": "none"})
        assert resp.status_code == 200
        assert resp.json()["automation"] == "none"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete(self, client: AsyncClient, repo: Repository):
        lib = await repo.create_library(name="x", path="/media/x")
        resp = await client.delete(f"libraries/{lib.id}")
        assert resp.status_code == 204

        resp = await client.get("libraries")
        assert resp.json()["items"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_not_found(self, client: AsyncClient):
        resp = await client.delete("libraries/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_cascades_media_files(self, client: AsyncClient, repo: Repository):
        """删除媒体库经 API 应级联删除其下 MediaFile 记录, 不留悬空引用."""
        lib = await repo.create_library(name="x", path="/media/x")
        assert lib.id is not None
        await repo.create_media_file(library_id=lib.id, path="/media/x/a.mp4")
        await repo.create_media_file(library_id=lib.id, path="/media/x/b.mp4")
        assert await repo.count_media_files(library_id=lib.id) == 2

        resp = await client.delete(f"libraries/{lib.id}")
        assert resp.status_code == 204

        assert await repo.count_media_files(library_id=lib.id) == 0
        assert await repo.get_library(lib.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_path_template_schema(self, client: AsyncClient):
        """path-template-schema 与 resolve_paths 同源: 含占位符相位与默认模板."""
        from amane.organize import CD_SUFFIX_TEMPLATE_DEFAULT, OPTIONAL_TEMPLATE_DEFAULTS, VIDEO_TEMPLATE_DEFAULT

        resp = await client.get("libraries/path-template-schema")
        assert resp.status_code == 200
        data = resp.json()
        assert data["video_default"] == VIDEO_TEMPLATE_DEFAULT
        assert data["cd_suffix_default"] == CD_SUFFIX_TEMPLATE_DEFAULT
        assert data["optional_defaults"] == OPTIONAL_TEMPLATE_DEFAULTS
        names = {p["name"] for p in data["placeholders"]}
        assert {"number", "studio", "video_dir", "dir", "ext", "mosaic", "definition"} <= names
        phases = {p["name"]: p["phase"] for p in data["placeholders"]}
        assert phases["number"] == "metadata"
        assert phases["dir"] == "source"
        assert phases["mosaic"] == "file"
        assert phases["definition"] == "file"
        assert phases["video_dir"] == "post_video"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_with_all_fields(self, client: AsyncClient, safe_path: Path):
        """创建媒体库时传入全部自定义模板字段"""
        target = safe_path / "full"
        target.mkdir()
        resp = await client.post(
            "libraries",
            json={
                "name": "Full Library",
                "path": str(target),
                "automation": "none",
                "recursive": False,
                "patterns": ["*.mp4"],
                "move_mode": "hardlink",
                "video_template": "/out/{number}/{number}.mp4",
                "poster_template": "/out/{number}/poster.jpg",
                "thumb_template": "/out/{number}/thumb.jpg",
                "fanart_template": "/out/{number}/fanart.jpg",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Full Library"
        assert data["move_mode"] == "hardlink"
        assert data["video_template"] == "/out/{number}/{number}.mp4"
        assert data["poster_template"] == "/out/{number}/poster.jpg"
        assert data["thumb_template"] == "/out/{number}/thumb.jpg"
        assert data["fanart_template"] == "/out/{number}/fanart.jpg"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_triggers_initial_scan_task(self, client: AsyncClient, repo: Repository, safe_path: Path):
        """创建媒体库后自动提交初始 Refresh 任务到数据库, payload 带 library_id 与 path, 仅执行文件扫描注册不执行刮削."""
        target = safe_path / "auto-scan"
        target.mkdir()
        resp = await client.post(
            "libraries",
            json={
                "path": str(target),
                "scan": True,
            },
        )
        assert resp.status_code == 201
        lib_id = resp.json()["id"]

        # 验证 Refresh 任务已创建
        tasks = await repo.list_tasks(task_types=[TaskType.REFRESH])
        scan_tasks = [t for t in tasks if t.payload.get("library_id") == lib_id]
        assert len(scan_tasks) == 1
        assert scan_tasks[0].payload["scan"] == ["add"]
        assert scan_tasks[0].payload["scrape"] == []
        assert scan_tasks[0].payload["path"] == str(target)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_without_initial_scan(self, client: AsyncClient, repo: Repository, safe_path: Path):
        """scan=False 时不提交扫描任务"""
        target = safe_path / "no-scan"
        target.mkdir()
        resp = await client.post("libraries", json={"path": str(target), "scan": False})
        assert resp.status_code == 201
        tasks = await repo.list_tasks(task_types=[TaskType.REFRESH])
        assert tasks == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_no_fields_rejected(self, client: AsyncClient, repo: Repository, safe_path: Path):
        """PUT 请求没有提供任何字段 → 422"""
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(f"libraries/{lib.id}", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_empty_patterns_is_list_not_null(self, client: AsyncClient, repo: Repository, safe_path: Path):
        """空 glob 写成 []; 显式 null 拒绝 (列是 list[str], 不是 Optional)."""
        lib = await repo.create_library(name="t", path=str(safe_path), patterns=["*.mp4"])
        cleared = await client.patch(f"libraries/{lib.id}", json={"patterns": []})
        assert cleared.status_code == 200
        assert cleared.json()["patterns"] == []
        rejected = await client.patch(f"libraries/{lib.id}", json={"patterns": None})
        assert rejected.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_move_mode(self, client: AsyncClient, repo: Repository, safe_path: Path):
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(f"libraries/{lib.id}", json={"move_mode": "symlink"})
        assert resp.status_code == 200
        assert resp.json()["move_mode"] == "symlink"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_rejects_invalid_move_mode(self, client: AsyncClient, safe_path: Path):
        target = safe_path / "bad-mode"
        target.mkdir()
        resp = await client.post("libraries", json={"path": str(target), "move_mode": "foobar"})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_rejects_invalid_trailer_pattern(self, client: AsyncClient, safe_path: Path):
        target = safe_path / "bad-re"
        target.mkdir()
        resp = await client.post("libraries", json={"path": str(target), "trailer_pattern": "[unclosed"})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_organize_settings(self, client: AsyncClient, repo: Repository, safe_path: Path):
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(
            f"libraries/{lib.id}", json={"write_nfo": False, "copy_resources": ["thumb"], "trailer_pattern": "预告"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["write_nfo"] is False
        assert data["copy_resources"] == ["thumb"]
        assert data["trailer_pattern"] == "预告"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_with_custom_cd_suffix(self, client: AsyncClient, safe_path: Path):
        """cd_suffix_template 可自定义 (如 -Part {cd}), 响应回显."""
        target = safe_path / "cd-suffix"
        target.mkdir()
        resp = await client.post("libraries", json={"path": str(target), "cd_suffix_template": "-Part {cd}"})
        assert resp.status_code == 201
        assert resp.json()["cd_suffix_template"] == "-Part {cd}"

        default_dir = target / "default"
        default_dir.mkdir()
        default = await client.post("libraries", json={"path": str(default_dir)})
        assert default.status_code == 201
        assert default.json()["cd_suffix_template"] == "-CD{cd}"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_rejects_invalid_cd_suffix(self, client: AsyncClient, safe_path: Path):
        target = safe_path / "bad-cd"
        target.mkdir()
        missing = await client.post("libraries", json={"path": str(target), "cd_suffix_template": "-CD"})
        assert missing.status_code == 422
        separator = await client.post("libraries", json={"path": str(target), "cd_suffix_template": "disc{cd}/x"})
        assert separator.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_cd_suffix(self, client: AsyncClient, repo: Repository, safe_path: Path):
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(f"libraries/{lib.id}", json={"cd_suffix_template": ""})
        assert resp.status_code == 200
        assert resp.json()["cd_suffix_template"] == ""
        rejected = await client.patch(f"libraries/{lib.id}", json={"cd_suffix_template": "no-placeholder"})
        assert rejected.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_rejects_invalid_trailer_pattern(self, client: AsyncClient, repo: Repository, safe_path: Path):
        lib = await repo.create_library(name="t", path=str(safe_path))
        resp = await client.patch(f"libraries/{lib.id}", json={"trailer_pattern": "[unclosed"})
        assert resp.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_allows_empty_trailer_pattern(self, client: AsyncClient, safe_path: Path):
        target = safe_path / "no-skip"
        target.mkdir()
        resp = await client.post("libraries", json={"path": str(target), "trailer_pattern": ""})
        assert resp.status_code == 201
        assert resp.json()["trailer_pattern"] == ""
