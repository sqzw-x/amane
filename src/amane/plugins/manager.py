"""Plugin discovery, source catalog, and configuration validation."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from pydantic import BaseModel

from ..config.manager import HotSettings
from ..crawlers import registry
from ..crawlers.site_roles import FILM_METADATA_SITES
from .api import FilmSourcePlugin, FilmSourceProvider, PluginContext
from .models import (
    PLUGIN_API_VERSION,
    PluginConfig,
    PluginOrigin,
    SourceCapability,
    SourceDescriptor,
    is_external_source_id,
    validate_external_source_id,
)
from .packaging import (
    PLUGIN_CLASS_NAME,
    load_plugin_module,
    module_name,
    plugin_entry,
    purge_imported_plugin_modules,
    sources_root,
)

logger = logging.getLogger(__name__)


class PluginLoadFailure(BaseModel):
    """A plugin drop-in that could not be loaded."""

    name: str
    value: str
    error: str


class PluginManager:
    """Catalog of built-in and discovered film sources.

    The catalog is replaced in-process on install, uninstall, or reload. Builtin
    crawlers stay in the in-tree registry; only drop-ins under ``plugins/sources`` change.
    """

    def __init__(
        self,
        plugins: dict[str, FilmSourcePlugin],
        failures: list[PluginLoadFailure],
        origins: dict[str, PluginOrigin] | None = None,
    ):
        self._plugins = plugins
        self._failures = failures
        self._origins = origins or {}
        self._descriptors = self._build_descriptors(plugins)

    @classmethod
    def discover(cls, data_dir: Path) -> PluginManager:
        """Discover third-party source plugins from ``{data_dir}/plugins/sources``."""
        purge_imported_plugin_modules()

        plugins: dict[str, FilmSourcePlugin] = {}
        origins: dict[str, PluginOrigin] = {}
        failures: list[PluginLoadFailure] = []
        builtin_ids = {str(site) for site in FILM_METADATA_SITES}
        root = sources_root(data_dir)
        root.mkdir(parents=True, exist_ok=True)

        for child in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
            entry = plugin_entry(child)
            try:
                plugin_id = child.name
                validate_external_source_id(plugin_id)
                if not entry.is_file():
                    raise FileNotFoundError(f"missing {entry.name}")
                module = load_plugin_module(plugin_id, child)
                candidate = module.__dict__.get(PLUGIN_CLASS_NAME)
                if not isinstance(candidate, type) or not issubclass(candidate, FilmSourcePlugin):
                    raise TypeError(f"plugin.py must define a FilmSourcePlugin subclass named {PLUGIN_CLASS_NAME}")
                plugin = candidate()
                descriptor = plugin.descriptor()
                if descriptor.api_version != PLUGIN_API_VERSION:
                    raise ValueError(
                        f"unsupported plugin API version {descriptor.api_version!r}; expected {PLUGIN_API_VERSION!r}"
                    )
                if not descriptor.supports(SourceCapability.FILM_METADATA):
                    raise ValueError("descriptor does not advertise the film_metadata capability")
                validate_external_source_id(descriptor.id)
                if descriptor.id != plugin_id:
                    raise ValueError(f"descriptor id {descriptor.id!r} does not match directory name {plugin_id!r}")
                if descriptor.id in builtin_ids or descriptor.id in plugins:
                    raise ValueError(f"source id {descriptor.id!r} is already registered")
                plugins[descriptor.id] = plugin
                origins[descriptor.id] = PluginOrigin(
                    plugin_id=descriptor.id, path=str(child), module=module_name(descriptor.id)
                )
            except Exception as exc:
                failure = PluginLoadFailure(name=child.name, value=str(entry), error=str(exc))
                failures.append(failure)
                logger.exception("source plugin load failed", extra={"plugin": child.name})

        return cls(plugins, failures, origins)

    @property
    def failures(self) -> tuple[PluginLoadFailure, ...]:
        return tuple(self._failures)

    @property
    def multi_language_sources(self) -> frozenset[str]:
        return frozenset(descriptor.id for descriptor in self.descriptors() if descriptor.multi_language)

    def get(self, source_id: str) -> FilmSourcePlugin | None:
        return self._plugins.get(source_id)

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    def origin(self, plugin_id: str) -> PluginOrigin | None:
        return self._origins.get(plugin_id)

    def origins(self) -> tuple[PluginOrigin, ...]:
        return tuple(self._origins.values())

    def plugin_ids(self) -> frozenset[str]:
        return frozenset(self._plugins)

    def descriptors(self) -> tuple[SourceDescriptor, ...]:
        return self._descriptors

    def plugin_descriptors(self) -> tuple[SourceDescriptor, ...]:
        """Return descriptors owned by discovered external plugins."""
        return tuple(descriptor for descriptor in self._descriptors if descriptor.id in self._plugins)

    def descriptor(self, source_id: str) -> SourceDescriptor | None:
        return next((item for item in self._descriptors if item.id == source_id), None)

    def plugin_config_schema(self, plugin_id: str) -> dict[str, object]:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        return plugin.configuration_model().model_json_schema()

    def validate_plugin_config(self, plugin_id: str, config: PluginConfig) -> BaseModel:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        return plugin.configuration_model().model_validate(config.config)

    def augment_config_schema(self, schema: dict[str, object]) -> dict[str, object]:
        """Add discovered source IDs to the static HotSettings schema."""
        out = copy.deepcopy(schema)
        defs = out.get("$defs")
        if not isinstance(defs, dict):
            return out
        scraping = defs.get("ScrapingConfig")
        if not isinstance(scraping, dict):
            return out
        properties = scraping.get("properties")
        if not isinstance(properties, dict):
            return out

        source_ids = [descriptor.id for descriptor in self.descriptors()]
        for field_name in ("content_routes", "field_priority", "field_blacklist"):
            field = properties.get(field_name)
            if not isinstance(field, dict):
                continue
            additional = field.get("additionalProperties")
            if not isinstance(additional, dict):
                continue
            items = additional.get("items")
            if not isinstance(items, dict):
                continue
            items["enum"] = source_ids
        return out

    def validate_hot_settings(self, hot: HotSettings, *, require_available: bool = True) -> None:
        """Validate routes and plugin configs against the session source catalog.

        Disabled plugins may remain in routes; Factory skips them at scrape time.
        Missing namespaced third-party IDs are warnings when ``require_available`` is false.
        """
        known = {descriptor.id: descriptor for descriptor in self.descriptors()}
        for content_type, route in hot.scraping.content_routes.items():
            field = f"scraping.content_routes.{content_type}"
            for source_id in route:
                descriptor = self._descriptor_for_route(
                    str(source_id), known, require_available=require_available, field=field
                )
                if descriptor is None:
                    continue
                if not descriptor.supports(SourceCapability.FILM_METADATA):
                    raise ValueError(f"source {source_id!r} cannot provide film metadata")
                if descriptor.content_types and content_type not in descriptor.content_types:
                    raise ValueError(f"source {source_id!r} does not support content type {content_type!r}")

        for prefix, mapping in (
            ("scraping.field_priority", hot.scraping.field_priority),
            ("scraping.field_blacklist", hot.scraping.field_blacklist),
        ):
            for meta_field, sources in mapping.items():
                field = f"{prefix}.{meta_field}"
                for source_id in sources:
                    descriptor = self._descriptor_for_route(
                        str(source_id), known, require_available=require_available, field=field
                    )
                    if descriptor is None:
                        continue
                    if not descriptor.supports(SourceCapability.FILM_METADATA):
                        raise ValueError(f"source {source_id!r} cannot provide film metadata")
                    if descriptor.metadata_fields and meta_field not in descriptor.metadata_fields:
                        raise ValueError(f"source {source_id!r} does not provide metadata field {meta_field!r}")

        for plugin_id, config in hot.plugins.items():
            if not is_external_source_id(plugin_id):
                raise ValueError(f"plugin configuration key must be a namespaced source id: {plugin_id!r}")
            if plugin_id not in self._plugins:
                if require_available:
                    raise ValueError(f"configuration exists for unavailable plugin {plugin_id!r}")
                continue
            if config.enabled:
                self.validate_plugin_config(plugin_id, config)

    @staticmethod
    def _descriptor_for_route(
        source_id: str,
        known: dict[str, SourceDescriptor],
        *,
        require_available: bool,
        field: str,
    ) -> SourceDescriptor | None:
        descriptor = known.get(source_id)
        if descriptor is not None:
            return descriptor
        if not require_available and is_external_source_id(source_id):
            logger.warning("source plugin unavailable in configuration", extra={"source": source_id, "field": field})
            return None
        raise ValueError(f"{field} references unavailable source {source_id!r}")

    def build_plugin_provider(
        self,
        plugin_id: str,
        *,
        context: PluginContext,
        config: PluginConfig,
    ) -> FilmSourceProvider:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        typed_config = self.validate_plugin_config(plugin_id, config)
        provider = plugin.build(context, typed_config)
        if not isinstance(provider, FilmSourceProvider):
            raise TypeError("plugin build() must return a FilmSourceProvider")
        return provider

    @staticmethod
    def _build_descriptors(plugins: dict[str, FilmSourcePlugin]) -> tuple[SourceDescriptor, ...]:
        builtin: list[SourceDescriptor] = []
        for site in FILM_METADATA_SITES:
            source_id = str(site)
            crawler = registry.get(source_id)
            if crawler is None:
                continue
            profile = crawler.profile()
            builtin.append(
                SourceDescriptor(
                    id=source_id,
                    name=source_id,
                    version="builtin",
                    capabilities=frozenset(profile.effective_capabilities()),
                    urls=(*profile.urls, profile.base_url),
                    multi_language=profile.multi_language,
                )
            )

        external = [plugin.descriptor() for plugin in plugins.values()]
        return tuple(sorted([*builtin, *external], key=lambda item: item.id))
