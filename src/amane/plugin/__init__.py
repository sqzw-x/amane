"""Author-facing film-source plugin SDK.

Plugin drop-ins should import only from this module. Host code uses
``amane.plugins.*`` implementation modules and must not import ``amane.plugin``.
This is a documentation and import-path boundary, not a runtime sandbox.
"""

from ..crawlers.http import HttpClient
from ..crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery, film_actors
from ..enums import Language
from ..net.errors import FailureReason, RequestError, SourceError
from ..net.http import WebClient
from ..parsing.file_info import ContentType
from ..plugins.api import EmptyPluginConfig, FilmSourcePlugin, FilmSourceProvider, PluginContext
from ..plugins.models import (
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
    "FilmActor",
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
    "film_actors",
    "is_external_source_id",
    "validate_external_source_id",
]
