"""Author-facing film-source plugin SDK.

Plugin drop-ins should import only from this module. Host code uses
``amane.plugins.*`` implementation modules and must not import ``amane.plugin``.
This is a documentation and import-path boundary, not a runtime sandbox.
"""

from amane.crawlers.http import HttpClient
from amane.crawlers.models import FetchOptions, MediaMetadata, SearchQuery
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

__all__ = [
    "PLUGIN_API_VERSION",
    "RESERVED_SOURCE_NAMESPACES",
    "ContentType",
    "EmptyPluginConfig",
    "FailureReason",
    "FetchOptions",
    "FilmSourcePlugin",
    "FilmSourceProvider",
    "HttpClient",
    "Language",
    "MediaMetadata",
    "PluginContext",
    "RequestError",
    "SearchQuery",
    "SourceCapability",
    "SourceDescriptor",
    "SourceError",
    "SourceId",
    "WebClient",
    "is_external_source_id",
    "validate_external_source_id",
]
