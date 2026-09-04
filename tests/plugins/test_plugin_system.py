"""Film source plugin API and runtime catalog tests."""

from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from amane.config import HotSettings, PluginConfig
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.http import HttpClient
from amane.crawlers.models import FetchOptions, MediaMetadata, SearchQuery
from amane.plugin import (
    FilmSourcePlugin,
    FilmSourceProvider,
    PluginContext,
    SourceCapability,
    SourceDescriptor,
    is_external_source_id,
    validate_external_source_id,
)
from amane.plugins.manager import PluginManager
from amane.plugins.packaging import PLUGIN_ENTRY, sources_root


def write_plugin(
    data_dir: Path,
    plugin_id: str,
    *,
    body: str | None = None,
    directory_name: str | None = None,
    api_version: str | None = None,
) -> Path:
    """Write ``plugin.py`` under ``plugins/sources/<directory_name>/``."""
    folder = directory_name or plugin_id
    plugin_dir = sources_root(data_dir) / folder
    plugin_dir.mkdir(parents=True, exist_ok=True)
    source = body if body is not None else plugin_source(plugin_id, api_version=api_version)
    (plugin_dir / PLUGIN_ENTRY).write_text(source, encoding="utf-8")
    return plugin_dir


def plugin_source(
    plugin_id: str,
    *,
    class_name: str = "Plugin",
    api_version: str | None = None,
    content_types: str = '{"censored"}',
) -> str:
    version_arg = f", api_version={api_version!r}" if api_version is not None else ""
    return f"""
from pydantic import BaseModel, ConfigDict

from amane.plugin import (
    FilmSourcePlugin,
    FilmSourceProvider,
    MediaMetadata,
    PluginContext,
    SearchQuery,
    SourceCapability,
    SourceDescriptor
)


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = "https://plugin.example.test"


class _Provider(FilmSourceProvider):
    async def fetch(self, query: SearchQuery, options=None) -> MediaMetadata:
        return MediaMetadata(number=query.number, title="Plugin result")


class {class_name}(FilmSourcePlugin):
    config_model = _Config

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id={plugin_id!r},
            name="Fake plugin",
            version="0.1.0",
            capabilities=frozenset({{SourceCapability.FILM_METADATA}}),
            content_types=frozenset({content_types}),
            urls=("https://plugin.example.test",),
            multi_language=True{version_arg},
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        return _Provider()
"""


class FakePluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = "https://plugin.example.test"


class FakeProvider(FilmSourceProvider):
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata:
        return MediaMetadata(number=query.number, title="Plugin result")


class FakePlugin(FilmSourcePlugin):
    config_model = FakePluginConfig

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="acme.fake",
            name="Fake plugin",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            content_types=frozenset({"censored"}),
            urls=("https://plugin.example.test",),
            multi_language=True,
        )

    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        assert context.source_id == "acme.fake"
        assert context.data_dir.name == "acme.fake"
        assert context.data_dir.parent.name == "plugins"
        assert context.data_dir.is_dir()
        assert isinstance(config, FakePluginConfig)
        return FakeProvider()


class _FakeHttp:
    web_client = object()


def plugin_manager() -> PluginManager:
    return PluginManager({"acme.fake": FakePlugin()}, [])


def test_plugin_manager_discovers_dropins(tmp_path: Path) -> None:
    write_plugin(tmp_path, "acme.fake")
    write_plugin(tmp_path, "acme.broken", body="class Plugin:\n    pass\n")
    write_plugin(tmp_path, "acme.old", api_version="0")
    write_plugin(tmp_path, "fakesource", directory_name="fakesource")
    write_plugin(tmp_path, "plugin.squat", directory_name="plugin.squat")

    manager = PluginManager.discover(tmp_path)
    assert manager.has_plugin("acme.fake")
    origin = manager.origin("acme.fake")
    assert origin is not None
    assert origin.plugin_id == "acme.fake"
    assert Path(origin.path).name == "acme.fake"
    names = {failure.name for failure in manager.failures}
    assert names == {"acme.broken", "acme.old", "fakesource", "plugin.squat"}
    assert any("FilmSourcePlugin" in failure.error for failure in manager.failures)
    assert any("unsupported plugin API version" in failure.error for failure in manager.failures)
    assert any("namespace.local" in failure.error for failure in manager.failures)
    assert any("reserved" in failure.error for failure in manager.failures)


def test_discover_rejects_descriptor_id_mismatch(tmp_path: Path) -> None:
    write_plugin(tmp_path, "acme.fake", directory_name="acme.other")
    manager = PluginManager.discover(tmp_path)
    assert not manager.has_plugin("acme.fake")
    assert not manager.has_plugin("acme.other")
    assert any("does not match directory name" in failure.error for failure in manager.failures)


def test_source_descriptor_rejects_unstable_ids() -> None:
    with pytest.raises(ValidationError, match="source id"):
        SourceDescriptor(id="FakePlugin", name="Invalid")


@pytest.mark.parametrize(
    ("source_id", "ok"),
    [
        pytest.param("acme.fake", True, id="namespaced"),
        pytest.param("acme.foo.bar", True, id="nested-local"),
        pytest.param("javdb", False, id="builtin-single-segment"),
        pytest.param("fakesource", False, id="unnamespaced"),
        pytest.param("plugin.fake", False, id="reserved-plugin"),
        pytest.param("amane.foo", False, id="reserved-amane"),
        pytest.param("javdb.mirror", False, id="builtin-as-namespace"),
        pytest.param("acme.", False, id="empty-local"),
        pytest.param(".fake", False, id="empty-namespace"),
    ],
)
def test_external_source_id_rules(source_id: str, ok: bool) -> None:
    assert is_external_source_id(source_id) is ok
    if ok:
        assert validate_external_source_id(source_id) == source_id
        return
    with pytest.raises(ValueError):
        validate_external_source_id(source_id)


@pytest.mark.parametrize(
    ("payload", "require_available", "error"),
    [
        pytest.param(
            {
                "scraping": {"content_routes": {"censored": ["acme.fake"]}},
                "plugins": {"acme.fake": {"enabled": False}},
            },
            True,
            None,
            id="disabled-plugin-may-stay-in-route",
        ),
        pytest.param(
            {
                "scraping": {"field_priority": {"title": ["acme.fake"]}},
                "plugins": {"acme.fake": {"enabled": False}},
            },
            True,
            None,
            id="disabled-plugin-may-stay-in-field-priority",
        ),
        pytest.param(
            {
                "scraping": {"field_blacklist": {"title": ["acme.fake"]}},
                "plugins": {"acme.fake": {"enabled": False}},
            },
            True,
            None,
            id="disabled-plugin-may-stay-in-field-blacklist",
        ),
        pytest.param(
            {"plugins": {"acme.fake": {"enabled": False, "config": {"unknown": True}}}},
            True,
            None,
            id="disabled-plugin-skips-config-validation",
        ),
        pytest.param(
            {"scraping": {"content_routes": {"censored": ["acme.gone"]}}},
            False,
            None,
            id="missing-plugin-allowed-when-optional",
        ),
        pytest.param(
            {"scraping": {"content_routes": {"censored": ["acme.gone"]}}},
            True,
            "unavailable source",
            id="missing-plugin-rejected-when-required",
        ),
        pytest.param(
            {"scraping": {"content_routes": {"fc2": ["acme.fake"]}}},
            True,
            "does not support content type",
            id="content-type-mismatch",
        ),
        pytest.param(
            {"plugins": {"javdb": {"enabled": True}}},
            True,
            "namespaced source id",
            id="plugin-key-must-be-namespaced",
        ),
        pytest.param(
            {"plugins": {"plugin.foo": {"enabled": True}}},
            True,
            "namespaced source id",
            id="plugin-prefix-is-reserved-namespace",
        ),
        pytest.param(
            {"plugins": {"acme.gone": {"enabled": True}}},
            True,
            "unavailable plugin",
            id="config-for-missing-plugin-rejected-when-required",
        ),
        pytest.param(
            {"plugins": {"acme.gone": {"enabled": True}}},
            False,
            None,
            id="config-for-missing-plugin-allowed-when-optional",
        ),
        pytest.param(
            {
                "scraping": {"field_priority": {"title": ["acme.gone"]}},
            },
            False,
            None,
            id="missing-plugin-in-field-priority-optional",
        ),
        pytest.param(
            {
                "scraping": {"field_priority": {"title": ["acme.gone"]}},
            },
            True,
            "unavailable source",
            id="missing-plugin-in-field-priority-required",
        ),
        pytest.param(
            {
                "scraping": {"field_blacklist": {"title": ["acme.gone"]}},
            },
            False,
            None,
            id="missing-plugin-in-field-blacklist-optional",
        ),
        pytest.param(
            {
                "scraping": {"field_blacklist": {"title": ["acme.gone"]}},
            },
            True,
            "unavailable source",
            id="missing-plugin-in-field-blacklist-required",
        ),
    ],
)
def test_validate_hot_settings(payload: dict[str, object], require_available: bool, error: str | None) -> None:
    hot = HotSettings.model_validate(payload)
    if error is None:
        plugin_manager().validate_hot_settings(hot, require_available=require_available)
        return
    with pytest.raises(ValueError, match=error):
        plugin_manager().validate_hot_settings(hot, require_available=require_available)


def test_validate_hot_settings_rejects_invalid_plugin_config() -> None:
    hot = HotSettings.model_validate({"plugins": {"acme.fake": {"config": {"unknown": True}}}})
    with pytest.raises(ValidationError):
        plugin_manager().validate_hot_settings(hot)


def test_plugin_manager_validates_routes_config_and_schema() -> None:
    manager = plugin_manager()
    hot = HotSettings.model_validate(
        {
            "scraping": {"content_routes": {"censored": ["acme.fake"]}},
            "plugins": {"acme.fake": {"config": {"endpoint": "https://configured.test"}}},
        }
    )

    manager.validate_hot_settings(hot)
    schema = manager.plugin_config_schema("acme.fake")
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    endpoint = properties.get("endpoint")
    assert isinstance(endpoint, dict)
    assert endpoint.get("default") == "https://plugin.example.test"


def test_plugin_manager_augments_route_schema() -> None:
    manager = plugin_manager()
    schema = manager.augment_config_schema(HotSettings.model_json_schema())
    defs = schema.get("$defs")
    assert isinstance(defs, dict)
    scraping = defs.get("ScrapingConfig")
    assert isinstance(scraping, dict)
    properties = scraping.get("properties")
    assert isinstance(properties, dict)
    content_routes = properties.get("content_routes")
    assert isinstance(content_routes, dict)
    additional = content_routes.get("additionalProperties")
    assert isinstance(additional, dict)
    items = additional.get("items")
    assert isinstance(items, dict)
    enum = items.get("enum")
    assert isinstance(enum, list)
    assert "acme.fake" in enum
    for field_name in ("field_priority", "field_blacklist"):
        field = properties.get(field_name)
        assert isinstance(field, dict)
        additional = field.get("additionalProperties")
        assert isinstance(additional, dict)
        items = additional.get("items")
        assert isinstance(items, dict)
        field_enum = items.get("enum")
        assert isinstance(field_enum, list)
        assert "acme.fake" in field_enum
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    plugins = properties.get("plugins")
    assert isinstance(plugins, dict)
    assert plugins.get("x-hidden") is True


@pytest.mark.asyncio
async def test_factory_builds_and_caches_plugin_provider(tmp_path: Path) -> None:
    manager = plugin_manager()
    factory = CrawlerFactory(
        cast(HttpClient, _FakeHttp()),
        data_dir=tmp_path,
        plugin_manager=manager,
        plugin_configs={"acme.fake": PluginConfig(config={"endpoint": "https://configured.test"})},
    )

    first = await factory.get("acme.fake")
    second = await factory.get("acme.fake")
    assert first is second
    assert first is not None
    result = await first.fetch(SearchQuery("TEST-001"))
    assert result is not None
    assert result.title == "Plugin result"

    crawlers = await factory.get_crawlers(["acme.fake"])
    assert crawlers["acme.fake"] is first


@pytest.mark.asyncio
async def test_disabled_plugin_is_not_available(tmp_path: Path) -> None:
    factory = CrawlerFactory(
        cast(HttpClient, _FakeHttp()),
        data_dir=tmp_path,
        plugin_manager=plugin_manager(),
        plugin_configs={"acme.fake": PluginConfig(enabled=False)},
    )
    assert await factory.get("acme.fake") is None
