"""On-disk plugin zip install, load, and module purge."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from amane.plugins.manager import PluginManager
from amane.plugins.packaging import (
    EXT_MODULE_PREFIX,
    MAX_ZIP_BYTES,
    PLUGIN_ENTRY,
    install_plugin_path,
    install_plugin_zip,
    module_name,
    purge_imported_plugin_modules,
    sources_root,
    uninstall_plugin_tree,
)
from tests.plugins.test_plugin_system import playback_plugin_source, plugin_source, write_plugin


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buf.getvalue()


def test_install_zip_at_root(tmp_path: Path) -> None:
    payload = _zip_bytes({PLUGIN_ENTRY: plugin_source("acme.fake")})
    plugin_id = install_plugin_zip(tmp_path, payload)
    assert plugin_id == "acme.fake"
    assert (sources_root(tmp_path) / "acme.fake" / PLUGIN_ENTRY).is_file()
    manager = PluginManager.discover(tmp_path)
    assert manager.has_plugin("acme.fake")


def test_install_zip_playback_only(tmp_path: Path) -> None:
    payload = _zip_bytes({PLUGIN_ENTRY: playback_plugin_source("acme.play")})
    plugin_id = install_plugin_zip(tmp_path, payload)
    assert plugin_id == "acme.play"
    manager = PluginManager.discover(tmp_path)
    assert manager.has_playback_plugin("acme.play")
    assert not manager.has_film_plugin("acme.play")


def test_install_zip_nested_folder(tmp_path: Path) -> None:
    payload = _zip_bytes({f"wrapper/{PLUGIN_ENTRY}": plugin_source("acme.extra")})
    plugin_id = install_plugin_zip(tmp_path, payload)
    assert plugin_id == "acme.extra"
    manager = PluginManager.discover(tmp_path)
    assert manager.has_plugin("acme.extra")


def test_install_replaces_existing(tmp_path: Path) -> None:
    install_plugin_zip(tmp_path, _zip_bytes({PLUGIN_ENTRY: plugin_source("acme.fake")}))
    updated = plugin_source("acme.fake").replace("Fake plugin", "Updated plugin")
    install_plugin_zip(tmp_path, _zip_bytes({PLUGIN_ENTRY: updated}))
    manager = PluginManager.discover(tmp_path)
    descriptor = manager.descriptor("acme.fake")
    assert descriptor is not None
    assert descriptor.name == "Updated plugin"


def test_install_rejects_path_traversal(tmp_path: Path) -> None:
    payload = _zip_bytes({"../evil.py": "print(1)\n", PLUGIN_ENTRY: plugin_source("acme.fake")})
    with pytest.raises(ValueError, match="非法路径"):
        install_plugin_zip(tmp_path, payload)


def test_install_rejects_oversized_zip(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="过大"):
        install_plugin_zip(tmp_path, b"x" * (MAX_ZIP_BYTES + 1))


def test_install_rejects_missing_entry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=PLUGIN_ENTRY):
        install_plugin_zip(tmp_path, _zip_bytes({"readme.txt": "nope"}))


def test_install_path_copies_directory(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "acme.fake"
    source.mkdir(parents=True)
    (source / PLUGIN_ENTRY).write_text(plugin_source("acme.fake"), encoding="utf-8")
    plugin_id = install_plugin_path(tmp_path, source)
    assert plugin_id == "acme.fake"
    dest = sources_root(tmp_path) / "acme.fake" / PLUGIN_ENTRY
    assert dest.is_file()
    assert source.is_dir()
    manager = PluginManager.discover(tmp_path)
    assert manager.has_plugin("acme.fake")


def test_install_path_from_zip_file(tmp_path: Path) -> None:
    zip_path = tmp_path / "acme.extra.zip"
    zip_path.write_bytes(_zip_bytes({PLUGIN_ENTRY: plugin_source("acme.extra")}))
    assert install_plugin_path(tmp_path, zip_path) == "acme.extra"
    assert PluginManager.discover(tmp_path).has_plugin("acme.extra")


def test_install_path_already_in_place(tmp_path: Path) -> None:
    dest = write_plugin(tmp_path, "acme.fake")
    assert install_plugin_path(tmp_path, dest) == "acme.fake"
    assert dest.is_dir()


def test_install_path_rejects_plain_file(tmp_path: Path) -> None:
    source = tmp_path / "readme.txt"
    source.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="目录或 zip"):
        install_plugin_path(tmp_path, source)


def test_install_path_rejects_dir_without_entry(tmp_path: Path) -> None:
    source = tmp_path / "empty"
    source.mkdir()
    with pytest.raises(ValueError, match=PLUGIN_ENTRY):
        install_plugin_path(tmp_path, source)


def test_uninstall_removes_sources_keeps_runtime_data(tmp_path: Path) -> None:
    install_plugin_zip(tmp_path, _zip_bytes({PLUGIN_ENTRY: plugin_source("acme.fake")}))
    runtime = tmp_path / "plugins" / "acme.fake"
    runtime.mkdir(parents=True)
    (runtime / "cache.txt").write_text("keep", encoding="utf-8")
    uninstall_plugin_tree(tmp_path, "acme.fake")
    assert not (sources_root(tmp_path) / "acme.fake").exists()
    assert (runtime / "cache.txt").read_text(encoding="utf-8") == "keep"


def test_relative_import_from_plugin_package(tmp_path: Path) -> None:
    plugin_dir = write_plugin(tmp_path, "acme.fake", body="")
    (plugin_dir / "_helper.py").write_text("VALUE = 'from-helper'\n", encoding="utf-8")
    (plugin_dir / PLUGIN_ENTRY).write_text(
        plugin_source("acme.fake") + "\nfrom ._helper import VALUE\nassert VALUE == 'from-helper'\n", encoding="utf-8"
    )
    manager = PluginManager.discover(tmp_path)
    assert manager.has_plugin("acme.fake")


def test_purge_only_drops_dynamic_plugin_modules() -> None:
    name = module_name("acme.fake")
    plugin_mod = ModuleType(name)
    nested = ModuleType(f"{name}._helper")
    sys.modules[name] = plugin_mod
    sys.modules[nested.__name__] = nested
    amane_plugins = sys.modules["amane.plugins"]
    try:
        purge_imported_plugin_modules()
        assert name not in sys.modules
        assert nested.__name__ not in sys.modules
        assert sys.modules["amane.plugins"] is amane_plugins
        assert not any(key.startswith(EXT_MODULE_PREFIX) for key in sys.modules)
    finally:
        sys.modules.pop(name, None)
        sys.modules.pop(nested.__name__, None)
