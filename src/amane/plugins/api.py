"""Host-side plugin contracts.

Plugin authors should import these types from ``amane.plugin``, not this module.
The first API version exposes film metadata sources and playback sources. A plugin
does not receive the repository, task worker, or FastAPI application.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..parsing.file_info import ContentType, Mosaic
from .models import PluginConfig, SourceDescriptor

if TYPE_CHECKING:
    from ..crawlers.http import HttpClient
    from ..crawlers.models import FetchOptions, MediaMetadata, SearchQuery
    from ..net.http import WebClient


class EmptyPluginConfig(BaseModel):
    """Default configuration model for plugins without user settings."""

    model_config = ConfigDict(extra="forbid")


class FilmSourceProvider(ABC):
    """Minimal runtime contract consumed by the aggregate engine."""

    @abstractmethod
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        """Fetch metadata for one structured search query."""
        ...


class PlaybackMediaFile(BaseModel):
    """Indexed video attached to the metadata entry being played."""

    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    size: int | None = None
    content_type: ContentType
    mosaic: Mosaic | None = None
    has_subtitle: bool = False
    definition: str | None = None
    library_id: int


class PlaybackQuery(BaseModel):
    """Host-assembled snapshot passed to a playback provider."""

    model_config = ConfigDict(extra="forbid")

    metadata_id: int
    number: str
    title: str | None = None
    actors: list[str] = Field(default_factory=list)
    studio: str | None = None
    publisher: str | None = None
    release: str | None = None
    runtime: int | None = None
    tags: list[str] = Field(default_factory=list)
    series: str | None = None
    plot: str | None = None
    directors: list[str] = Field(default_factory=list)
    source_urls: dict[str, str] = Field(default_factory=dict)
    external_ids: dict[str, str] = Field(default_factory=dict)
    files: tuple[PlaybackMediaFile, ...] = ()
    selected_file_id: int | None = None


class PlaybackOffer(BaseModel):
    """Result of ``probe``: whether this source can play the query."""

    model_config = ConfigDict(extra="forbid")

    name: str
    content_type: str
    seekable: bool = True
    media_file_id: int | None = None


class FilePlaybackTarget(BaseModel):
    """Local indexed file. Only the builtin ``local`` source may produce this."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["file"] = "file"
    path: Path
    content_type: str
    size: int | None = None
    media_file_id: int


class UpstreamPlaybackTarget(BaseModel):
    """HTTP origin fetched by the host reverse proxy."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["upstream"] = "upstream"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None


class HlsPlaybackTarget(BaseModel):
    """HLS presentation. Host rejects this until playlist rewrite lands."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["hls"] = "hls"


PlaybackTarget = FilePlaybackTarget | UpstreamPlaybackTarget | HlsPlaybackTarget


class PlaybackProvider(ABC):
    """Runtime contract consumed by the playback factory."""

    @abstractmethod
    async def probe(self, query: PlaybackQuery) -> PlaybackOffer | None:
        """Return an offer without transferring media body. ``None`` = no stream."""
        ...

    @abstractmethod
    async def resolve(self, query: PlaybackQuery) -> PlaybackTarget | None:
        """Return the host-executed playback target. ``None`` = no stream."""
        ...


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Core services intentionally available to an in-process plugin.

    ``data_dir`` is a per-plugin subdirectory of the process data dir
    (``{data_dir}/plugins/<plugin_id>``). Plugins must not write outside it.
    """

    source_id: str
    http_client: HttpClient
    web_client: WebClient
    data_dir: Path


class _ConfiguredPlugin(ABC):
    """Shared descriptor and configuration surface for drop-in plugins."""

    config_model: ClassVar[type[BaseModel]] = EmptyPluginConfig

    @classmethod
    @abstractmethod
    def descriptor(cls) -> SourceDescriptor:
        """Return the stable source descriptor."""
        ...

    @classmethod
    def configuration_model(cls) -> type[BaseModel]:
        """Return the Pydantic model used to validate the plugin ``config`` object."""
        return cls.config_model

    def validate_config(self, value: dict[str, object]) -> BaseModel:
        """Validate a persisted plugin config envelope and return the typed settings."""
        envelope = PluginConfig.model_validate(value)
        return self.configuration_model().model_validate(envelope.config)


class FilmSourcePlugin(_ConfiguredPlugin):
    """Base class implemented by third-party film metadata plugins.

    Each drop-in directory ``{data_dir}/plugins/sources/<id>/`` must contain
    ``plugin.py`` exporting a subclass of this class named ``Plugin``.
    Authors import the subclass from ``amane.plugin``. The class is instantiated
    when the source catalog is discovered (startup, install, uninstall, or reload);
    providers are then cached by ``CrawlerFactory`` until the next rebuild.
    """

    @abstractmethod
    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        """Build a film metadata provider using core services and validated configuration."""
        ...


class PlaybackPlugin(_ConfiguredPlugin):
    """Base class implemented by third-party playback source plugins.

    Same drop-in layout as film sources. ``build_playback`` is a distinct method
    so a class may inherit both bases without colliding return types.
    """

    @abstractmethod
    def build_playback(self, context: PluginContext, config: BaseModel) -> PlaybackProvider:
        """Build a playback provider using core services and validated configuration."""
        ...


InstalledPlugin = FilmSourcePlugin | PlaybackPlugin
