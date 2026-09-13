"""Install, load, and remove on-disk source plugin trees."""

from __future__ import annotations

import importlib
import importlib.util
import io
import shutil
import sys
import zipfile
from pathlib import Path
from types import ModuleType

from .api import FilmSourcePlugin, PlaybackPlugin
from .models import validate_external_source_id

PLUGIN_ENTRY = "plugin.py"
PLUGIN_CLASS_NAME = "Plugin"
SOURCES_DIRNAME = "sources"
EXT_MODULE_PREFIX = "amane_ext_"
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_SKIP_EXTRACT_DIRS = frozenset({"__macosx", ".ds_store"})


def sources_root(data_dir: Path) -> Path:
    """Return ``{data_dir}/plugins/sources`` (plugin code trees)."""
    return data_dir / "plugins" / SOURCES_DIRNAME


def module_name(plugin_id: str) -> str:
    """Stable, unique sys.modules key for one source id (ids may contain '.' / '-')."""
    encoded = plugin_id.replace("_", "_u_").replace("-", "_h_").replace(".", "_d_")
    return f"{EXT_MODULE_PREFIX}{encoded}"


def plugin_entry(plugin_dir: Path) -> Path:
    return plugin_dir / PLUGIN_ENTRY


def purge_imported_plugin_modules() -> None:
    """Drop dynamically loaded plugin modules so the next discover() can reload them."""
    doomed = [name for name in sys.modules if name == "amane_ext" or name.startswith(EXT_MODULE_PREFIX)]
    for name in doomed:
        del sys.modules[name]
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()


def load_plugin_module(plugin_id: str, directory: Path) -> ModuleType:
    """Load ``plugin.py`` as a package rooted at ``directory``."""
    entry = plugin_entry(directory)
    if not entry.is_file():
        raise FileNotFoundError(f"missing {PLUGIN_ENTRY}")
    name = module_name(plugin_id)
    spec = importlib.util.spec_from_file_location(name, entry, submodule_search_locations=[str(directory)])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def inspect_plugin_id(directory: Path) -> str:
    """Load a staging copy of plugin.py and return ``Plugin.descriptor().id``."""
    staging_id = "_staging.inspect"
    try:
        module = load_plugin_module(staging_id, directory)
        plugin_id = _descriptor_id_from_module(module)
        validate_external_source_id(plugin_id)
        return plugin_id
    finally:
        staging = module_name(staging_id)
        sys.modules.pop(staging, None)
        for name in list(sys.modules):
            if name.startswith(f"{staging}."):
                del sys.modules[name]


def install_plugin_zip(data_dir: Path, payload: bytes) -> str:
    """Extract a zip into ``plugins/sources/<id>/`` and return the source id.

    The zip must contain ``plugin.py`` at the root or in a single top-level folder.
    An existing tree with the same id is replaced.
    """
    if len(payload) > MAX_ZIP_BYTES:
        raise ValueError("zip 过大")
    root = sources_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _extract_zip(payload, staging)
        plugin_dir = _plugin_dir_from_extract(staging)
        plugin_id = inspect_plugin_id(plugin_dir)
        _commit_plugin_dir(root, plugin_dir, plugin_id)
        return plugin_id
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def install_plugin_path(data_dir: Path, source: Path) -> str:
    """Copy a server-side plugin directory or zip into ``plugins/sources/<id>/``."""
    resolved = source.resolve()
    if resolved.is_file():
        if resolved.suffix.casefold() != ".zip":
            raise ValueError("只接受插件目录或 zip 文件")
        return install_plugin_zip(data_dir, resolved.read_bytes())
    if not resolved.is_dir():
        raise ValueError("只接受插件目录或 zip 文件")
    if not plugin_entry(resolved).is_file():
        raise ValueError(f"目录中必须包含 {PLUGIN_ENTRY}")
    plugin_id = inspect_plugin_id(resolved)
    root = sources_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _commit_plugin_dir(root, resolved, plugin_id)
    return plugin_id


def _commit_plugin_dir(root: Path, plugin_dir: Path, plugin_id: str) -> None:
    dest = root / plugin_id
    resolved_src = plugin_dir.resolve()
    resolved_dest = dest.resolve() if dest.exists() else dest
    if dest.exists() and resolved_src == resolved_dest:
        return
    if dest.exists() and resolved_dest.is_relative_to(resolved_src):
        raise ValueError("不能从包含安装目标的目录安装")
    incoming = root / f".{plugin_id}.new"
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.copytree(plugin_dir, incoming)
    old = root / f".{plugin_id}.old"
    if old.exists():
        shutil.rmtree(old)
    if dest.exists():
        dest.rename(old)
    incoming.rename(dest)
    if old.exists():
        shutil.rmtree(old)


def uninstall_plugin_tree(data_dir: Path, plugin_id: str) -> None:
    """Remove ``plugins/sources/<plugin_id>/``. Runtime data under ``plugins/<id>/`` is kept."""
    validate_external_source_id(plugin_id)
    dest = sources_root(data_dir) / plugin_id
    if not dest.is_dir():
        raise FileNotFoundError(plugin_id)
    shutil.rmtree(dest)


def _descriptor_id_from_module(module: ModuleType) -> str:
    candidate = module.__dict__.get(PLUGIN_CLASS_NAME)
    if not isinstance(candidate, type) or not (
        issubclass(candidate, FilmSourcePlugin) or issubclass(candidate, PlaybackPlugin)
    ):
        raise TypeError(
            f"{PLUGIN_ENTRY} must define a FilmSourcePlugin or PlaybackPlugin subclass named {PLUGIN_CLASS_NAME}"
        )
    return candidate().descriptor().id


def _plugin_dir_from_extract(root: Path) -> Path:
    if plugin_entry(root).is_file():
        return root
    children = [path for path in root.iterdir() if path.is_dir() and path.name.casefold() not in _SKIP_EXTRACT_DIRS]
    files = [path for path in root.iterdir() if path.is_file() and path.name != ".DS_Store"]
    if len(children) == 1 and not files and plugin_entry(children[0]).is_file():
        return children[0]
    raise ValueError(f"zip 必须在根目录或单一顶层文件夹中包含 {PLUGIN_ENTRY}")


def _extract_zip(payload: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        total = 0
        for info in archive.infolist():
            name = Path(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("zip 含有非法路径")
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("zip 解压后过大")
        archive.extractall(dest)
