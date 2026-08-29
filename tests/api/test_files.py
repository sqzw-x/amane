"""/files 端点测试 - 文件浏览器"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient


def _canonical_path(p: Path) -> str:
    """/files 回传的规范路径形态 (routes/files.py:_canonical_response_path).

    as_posix + 去 ``\\?\\`` 设备前缀; Windows 下与 ``str(Path)`` (原生 ``\\``) 不同.
    """
    s = str(p.resolve().as_posix())
    if s.startswith("//?/UNC/"):
        return "//" + s[len("//?/UNC/") :]
    if s.startswith("//?/"):
        return s[len("//?/") :]
    return s


class TestListFiles:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_directory(self, client: AsyncClient, safe_path):
        (safe_path / "z_dir").mkdir()
        (safe_path / "a_file.txt").write_text("x")
        (safe_path / "data.bin").write_bytes(b"\x00" * 100)
        (safe_path / ".hidden").write_text("secret")
        listed_file = safe_path / "somefile.txt"
        listed_file.write_text("content")

        resp = await client.get("files", params={"path": str(safe_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == _canonical_path(safe_path)
        items = body["items"]
        names = {e["name"] for e in items}
        assert names == {"z_dir", "a_file.txt", "data.bin", "somefile.txt"}
        assert items[0]["type"] == "directory"

        file_entry = next(e for e in items if e["name"] == "data.bin")
        assert file_entry["size"] == 100
        dir_entry = next(e for e in items if e["name"] == "z_dir")
        assert dir_entry["size"] is None

        shown = await client.get("files", params={"path": str(safe_path), "show_hidden": True})
        assert ".hidden" in {e["name"] for e in shown.json()["items"]}

        parent = await client.get("files", params={"path": str(listed_file)})
        assert parent.status_code == 200
        assert "somefile.txt" in {e["name"] for e in parent.json()["items"]}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_relative_path_returns_canonical_absolute(self, client: AsyncClient, safe_path):
        """相对输入 (首次起点 ".") 响应回传 resolve 后的规范绝对路径."""
        resp = await client.get("files", params={"path": "."})
        assert resp.status_code == 200
        assert resp.json()["path"] == _canonical_path(safe_path)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_relative_path_with_base(self, client: AsyncClient, safe_path):
        """相对输入以 base (当前浏览目录) 为基准, 回传规范绝对路径."""
        target = safe_path / "sub"
        target.mkdir()
        resp = await client.get("files", params={"path": "sub", "base": str(safe_path)})
        assert resp.status_code == 200
        assert resp.json()["path"] == _canonical_path(target)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_relative_path_escapes_base_rejected(self, client: AsyncClient, safe_path):
        """相对输入经 base 越界 ("..") 与绝对路径同一安全边界 → 403."""
        resp = await client.get("files", params={"path": "..", "base": str(safe_path)})
        assert resp.status_code == 403

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_rejects_illegal_paths(self, client: AsyncClient, safe_path, tmp_path):
        outside = await client.get("files", params={"path": str(tmp_path)})
        assert outside.status_code == 403
        missing = await client.get("files", params={"path": str(safe_path / "nope")})
        assert missing.status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_virtual_volume_literal_resolution(self, client: AsyncClient, safe_path, monkeypatch):
        """虚拟挂载盘 (CloudDrive2 类) 无法规范化查询、只返回字面路径时仍可浏览 (issue #8)."""
        (safe_path / "movies").mkdir()

        def fake_realpath(path, *, strict=False):
            # 模拟此类卷的容错结果: 只给出输入的字面绝对路径
            return os.path.abspath(os.fspath(path))  # noqa: PTH100

        monkeypatch.setattr(os.path, "realpath", fake_realpath)
        resp = await client.get("files", params={"path": str(safe_path / "movies")})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == _canonical_path(safe_path / "movies")
        assert body["items"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_scandir_oserror_maps_to_500(self, client: AsyncClient, safe_path, monkeypatch):
        """网络盘挂载失效 (macOS errno 6) 等 OSError 不再是裸 500 无日志."""

        def fake_scandir(path):
            raise OSError(6, "Device not configured")

        monkeypatch.setattr("amane.api.routes.files.os.scandir", fake_scandir)
        resp = await client.get("files", params={"path": str(safe_path)})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "Device not configured" in detail

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_entry_stat_error_skips_metadata(self, client: AsyncClient, safe_path, monkeypatch):
        """单个条目 stat 失败只丢该条元数据, 不影响整体列表 (网络盘瞬断场景)."""

        class FakeEntry:
            def __init__(self, name: str, path: Path) -> None:
                self.name = name
                self.path = str(path)

            def stat(self):
                raise OSError(5, "Input/output error")

        fake = FakeEntry("gone.txt", safe_path / "gone.txt")

        def fake_scandir(path):
            return iter([fake])

        monkeypatch.setattr("amane.api.routes.files.os.scandir", fake_scandir)
        resp = await client.get("files", params={"path": str(safe_path)})
        assert resp.status_code == 200
        assert resp.json()["items"] == [
            {
                "name": "gone.txt",
                "path": _canonical_path(safe_path / "gone.txt"),
                "type": "file",
                "size": None,
                "last_modified": None,
            }
        ]


class TestListFilesAllowAll:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_lists_path_outside_files_dir(self, allow_all_client: AsyncClient, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "movie").mkdir()
        resp = await allow_all_client.get("files", params={"path": str(outside)})
        assert resp.status_code == 200
        assert resp.json()["path"] == _canonical_path(outside)
        assert "movie" in {e["name"] for e in resp.json()["items"]}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_relative_parent_not_forbidden(self, allow_all_client: AsyncClient, tmp_path: Path):
        nested = tmp_path / "elsewhere" / "nested"
        nested.mkdir(parents=True)
        resp = await allow_all_client.get("files", params={"path": "..", "base": str(nested)})
        assert resp.status_code == 200
        assert resp.json()["path"] == _canonical_path(nested.parent)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_still_404(self, allow_all_client: AsyncClient, tmp_path: Path):
        resp = await allow_all_client.get("files", params={"path": str(tmp_path / "nope")})
        assert resp.status_code == 404
