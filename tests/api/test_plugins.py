"""External source plugin API endpoints."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from amane.config import HotSettings
from tests.api.conftest import make_app
from tests.plugins.test_plugin_system import plugin_source, write_plugin


def _plugin_zip(plugin_id: str, *, extra: str = "") -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        archive.writestr("plugin.py", plugin_source(plugin_id) + extra)
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
    async def test_list_patch_uninstall(self, client):
        listed = await client.get("plugins")
        assert listed.status_code == 200
        data = listed.json()
        assert [item["descriptor"]["id"] for item in data["items"]] == ["acme.fake"]
        assert data["failures"] == []
        assert data["items"][0]["config"]["enabled"] is True
        assert data["items"][0]["path"] is not None
        assert data["items"][0]["path"].endswith("acme.fake")

        assert (await client.get("plugins/acme.missing")).status_code == 404
        assert (await client.patch("plugins/acme.missing", json={"enabled": False, "config": {}})).status_code == 404

        updated = await client.patch("plugins/acme.fake", json={"config": {"endpoint": "https://configured.test"}})
        assert updated.status_code == 200
        assert updated.json()["config"]["config"]["endpoint"] == "https://configured.test"
        assert updated.json()["config"]["enabled"] is True
        assert (await client.patch("plugins/acme.fake", json={"config": {"unknown": True}})).status_code == 422

        disabled = await client.patch("plugins/acme.fake", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["config"]["enabled"] is False
        assert (await client.get("plugins")).json()["items"][0]["config"]["enabled"] is False

        config_resp = await client.patch(
            "config", json={"scraping": {"content_routes": {"fc2": ["acme.gone", "javdb"]}}}
        )
        assert config_resp.status_code == 200
        still = await client.patch("plugins/acme.fake", json={"config": {"endpoint": "https://still-ok.test"}})
        assert still.status_code == 200
        assert still.json()["config"]["config"]["endpoint"] == "https://still-ok.test"

        assert (await client.delete("plugins/acme.fake")).status_code == 204
        assert (await client.get("plugins")).json()["items"] == []
        assert (await client.delete("plugins/acme.missing")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_install_and_reload(self, client, tmp_path: Path):
        uploaded = await client.post(
            "plugins", files={"file": ("extra.zip", _plugin_zip("acme.extra"), "application/zip")}
        )
        assert uploaded.status_code == 201, uploaded.text
        assert [item["descriptor"]["id"] for item in uploaded.json()["items"]] == ["acme.extra", "acme.fake"]

        source = tmp_path / "files" / "acme.fromdir"
        source.mkdir(parents=True)
        (source / "plugin.py").write_text(plugin_source("acme.fromdir"), encoding="utf-8")
        from_dir = await client.post("plugins", data={"path": str(source)})
        assert from_dir.status_code == 201, from_dir.text

        zip_path = tmp_path / "files" / "fromzip.zip"
        zip_path.write_bytes(_plugin_zip("acme.fromzip"))
        from_zip = await client.post("plugins", data={"path": str(zip_path)})
        assert from_zip.status_code == 201, from_zip.text

        assert (await client.post("plugins")).status_code == 422
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "plugin.py").write_text(plugin_source("acme.extra2"), encoding="utf-8")
        assert (await client.post("plugins", data={"path": str(outside)})).status_code == 403

        not_zip = await client.post("plugins", files={"file": ("plugin.py", b"class Plugin: pass\n", "text/plain")})
        assert not_zip.status_code == 422
        assert "zip" in not_zip.json()["detail"]

        buf = BytesIO()
        with ZipFile(buf, "w") as archive:
            archive.writestr("readme.txt", "nope")
        assert (
            await client.post("plugins", files={"file": ("empty.zip", buf.getvalue(), "application/zip")})
        ).status_code == 422

        unimportable = await client.post(
            "plugins",
            files={
                "file": (
                    "unimportable.zip",
                    _plugin_zip("acme.broken", extra="\nimport amane_missing_test_dependency\n"),
                    "application/zip",
                )
            },
        )
        assert unimportable.status_code == 422, unimportable.text
        assert "amane_missing_test_dependency" in unimportable.json()["detail"]

        write_plugin(tmp_path / "data", "acme.dropin")
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200
        ids = {item["descriptor"]["id"] for item in reloaded.json()["items"]}
        assert "acme.dropin" in ids
        assert "acme.fake" in ids

    @pytest.mark.asyncio(loop_scope="function")
    async def test_allow_all_installs_path_outside_files_dir(self, allow_all_client, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "plugin.py").write_text(plugin_source("acme.extra"), encoding="utf-8")
        response = await allow_all_client.post("plugins", data={"path": str(outside)})
        assert response.status_code == 201, response.text
        ids = [item["descriptor"]["id"] for item in response.json()["items"]]
        assert "acme.extra" in ids
