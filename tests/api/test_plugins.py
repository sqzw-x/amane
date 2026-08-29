"""External source plugin API endpoints."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from amane.config import HotSettings
from tests.api.conftest import make_app
from tests.plugins.test_plugin_system import plugin_source, write_plugin


def _plugin_zip(plugin_id: str) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        archive.writestr("plugin.py", plugin_source(plugin_id))
    return buf.getvalue()


@pytest.fixture
def app(tmp_path: Path):
    data_dir = tmp_path / "data"
    write_plugin(data_dir, "acme.fake")
    hot = HotSettings.model_validate(
        {
            "scraping": {"content_routes": {"censored": ["acme.fake", "javdb"]}},
            "plugins": {"acme.fake": {"config": {"endpoint": "https://plugin.example.test"}}},
        }
    )
    return make_app(hot, data_dir, tmp_path / "logs", tmp_path / "files")


class TestPluginsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_plugins(self, client):
        response = await client.get("plugins")
        assert response.status_code == 200
        data = response.json()
        assert [item["descriptor"]["id"] for item in data["items"]] == ["acme.fake"]
        assert data["failures"] == []
        assert data["items"][0]["config"]["enabled"] is True
        assert data["items"][0]["path"] is not None
        assert data["items"][0]["path"].endswith("acme.fake")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_missing_plugin(self, client):
        response = await client.get("plugins/acme.missing")
        assert response.status_code == 404
        assert response.json()["detail"] == "插件不存在"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_missing_plugin(self, client):
        response = await client.patch("plugins/acme.missing", json={"enabled": False, "config": {}})
        assert response.status_code == 404
        assert response.json()["detail"] == "插件不存在"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_config(self, client):
        response = await client.patch("plugins/acme.fake", json={"config": {"endpoint": "https://configured.test"}})
        assert response.status_code == 200
        data = response.json()
        assert data["config"]["config"]["endpoint"] == "https://configured.test"
        assert data["config"]["enabled"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disable_plugin_in_route(self, client):
        response = await client.patch("plugins/acme.fake", json={"enabled": False})
        assert response.status_code == 200
        assert response.json()["config"]["enabled"] is False

        listed = await client.get("plugins")
        assert listed.json()["items"][0]["config"]["enabled"] is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_config_returns_422(self, client):
        response = await client.patch("plugins/acme.fake", json={"config": {"unknown": True}})
        assert response.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_succeeds_when_unrelated_plugin_missing(self, client):
        config_resp = await client.patch(
            "config", json={"scraping": {"content_routes": {"fc2": ["acme.gone", "javdb"]}}}
        )
        assert config_resp.status_code == 200
        response = await client.patch("plugins/acme.fake", json={"config": {"endpoint": "https://still-ok.test"}})
        assert response.status_code == 200
        assert response.json()["config"]["config"]["endpoint"] == "https://still-ok.test"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_plugin(self, client):
        response = await client.post(
            "plugins", files={"file": ("extra.zip", _plugin_zip("acme.extra"), "application/zip")}
        )
        assert response.status_code == 201, response.text
        ids = [item["descriptor"]["id"] for item in response.json()["items"]]
        assert ids == ["acme.extra", "acme.fake"]
        listed = await client.get("plugins")
        assert [item["descriptor"]["id"] for item in listed.json()["items"]] == ["acme.extra", "acme.fake"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_from_server_directory(self, client, tmp_path: Path):
        source = tmp_path / "files" / "acme.extra"
        source.mkdir(parents=True)
        (source / "plugin.py").write_text(plugin_source("acme.extra"), encoding="utf-8")
        response = await client.post("plugins", data={"path": str(source)})
        assert response.status_code == 201, response.text
        ids = [item["descriptor"]["id"] for item in response.json()["items"]]
        assert ids == ["acme.extra", "acme.fake"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_from_server_zip(self, client, tmp_path: Path):
        zip_path = tmp_path / "files" / "extra.zip"
        zip_path.write_bytes(_plugin_zip("acme.extra"))
        response = await client.post("plugins", data={"path": str(zip_path)})
        assert response.status_code == 201, response.text
        assert [item["descriptor"]["id"] for item in response.json()["items"]] == ["acme.extra", "acme.fake"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_rejects_empty(self, client):
        response = await client.post("plugins")
        assert response.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_rejects_path_outside_safe_dirs(self, client, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "plugin.py").write_text(plugin_source("acme.extra"), encoding="utf-8")
        response = await client.post("plugins", data={"path": str(outside)})
        assert response.status_code == 403

    @pytest.mark.asyncio(loop_scope="function")
    async def test_allow_all_installs_path_outside_files_dir(self, allow_all_client, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "plugin.py").write_text(plugin_source("acme.extra"), encoding="utf-8")
        response = await allow_all_client.post("plugins", data={"path": str(outside)})
        assert response.status_code == 201, response.text
        ids = [item["descriptor"]["id"] for item in response.json()["items"]]
        assert "acme.extra" in ids

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_rejects_non_zip(self, client):
        response = await client.post("plugins", files={"file": ("plugin.py", b"class Plugin: pass\n", "text/plain")})
        assert response.status_code == 422
        assert "zip" in response.json()["detail"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_rejects_zip_without_entry(self, client):
        buf = BytesIO()
        with ZipFile(buf, "w") as archive:
            archive.writestr("readme.txt", "nope")
        response = await client.post("plugins", files={"file": ("empty.zip", buf.getvalue(), "application/zip")})
        assert response.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reload_discovers_new_dropin(self, client, tmp_path: Path):
        write_plugin(tmp_path / "data", "acme.extra")
        response = await client.post("plugins/reload")
        assert response.status_code == 200
        ids = [item["descriptor"]["id"] for item in response.json()["items"]]
        assert ids == ["acme.extra", "acme.fake"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uninstall_plugin(self, client):
        response = await client.delete("plugins/acme.fake")
        assert response.status_code == 204
        listed = await client.get("plugins")
        assert listed.json()["items"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uninstall_missing_plugin(self, client):
        response = await client.delete("plugins/acme.missing")
        assert response.status_code == 404
        assert response.json()["detail"] == "插件不存在"
