"""Author SDK re-exports the host contract types by identity."""

import amane.plugin as sdk
from amane.crawlers.http import HttpClient
from amane.crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery, film_actors
from amane.enums import Language
from amane.net.errors import FailureReason, RequestError, SourceError
from amane.net.http import WebClient
from amane.parsing.file_info import ContentType
from amane.plugins.api import EmptyPluginConfig, FilmSourcePlugin, FilmSourceProvider, PluginContext
from amane.plugins.models import (
    PLUGIN_API_VERSION,
    RESERVED_SOURCE_NAMESPACES,
    SourceCapability,
    SourceDescriptor,
    SourceId,
    is_external_source_id,
    validate_external_source_id,
)

_REEXPORTS: tuple[tuple[str, object], ...] = (
    ("PLUGIN_API_VERSION", PLUGIN_API_VERSION),
    ("RESERVED_SOURCE_NAMESPACES", RESERVED_SOURCE_NAMESPACES),
    ("ContentType", ContentType),
    ("EmptyPluginConfig", EmptyPluginConfig),
    ("FailureReason", FailureReason),
    ("FetchOptions", FetchOptions),
    ("FilmActor", FilmActor),
    ("FilmSourcePlugin", FilmSourcePlugin),
    ("FilmSourceProvider", FilmSourceProvider),
    ("HttpClient", HttpClient),
    ("Language", Language),
    ("MediaMetadata", MediaMetadata),
    ("PluginContext", PluginContext),
    ("RequestError", RequestError),
    ("SearchQuery", SearchQuery),
    ("SourceCapability", SourceCapability),
    ("SourceDescriptor", SourceDescriptor),
    ("SourceError", SourceError),
    ("SourceId", SourceId),
    ("WebClient", WebClient),
    ("film_actors", film_actors),
    ("is_external_source_id", is_external_source_id),
    ("validate_external_source_id", validate_external_source_id),
)


def test_sdk_all_matches_reexport_table() -> None:
    assert sdk.__all__ == [name for name, _ in _REEXPORTS]


def test_sdk_reexports_are_host_objects() -> None:
    for name, expected in _REEXPORTS:
        assert getattr(sdk, name) is expected


def test_sdk_hides_host_runtime() -> None:
    for name in ("PluginManager", "PluginOrigin", "PluginConfig", "PluginLoadFailure"):
        assert name not in sdk.__all__
        assert not hasattr(sdk, name)
