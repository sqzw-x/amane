"""Author SDK re-exports the host contract types by identity."""

import pytest
from pydantic import ValidationError

import amane.plugin as sdk
from amane.crawlers.http import HttpClient
from amane.crawlers.models import FetchOptions, FilmActor, MediaMetadata, SearchQuery, film_actors
from amane.enums import Language
from amane.net.errors import FailureReason, RequestError, SourceError
from amane.net.http import WebClient
from amane.parsing.file_info import ContentType
from amane.plugins.api import (
    EmptyPluginConfig,
    FilePlaybackTarget,
    FilmSourcePlugin,
    FilmSourceProvider,
    HlsLocator,
    HlsPlaybackTarget,
    HlsPlaylist,
    PlaybackMediaFile,
    PlaybackOffer,
    PlaybackPlugin,
    PlaybackProvider,
    PlaybackQuery,
    PluginContext,
    RelativeHlsLocator,
    SubtitleTrack,
    UpstreamPlaybackTarget,
)
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
    ("FilePlaybackTarget", FilePlaybackTarget),
    ("FilmActor", FilmActor),
    ("FilmSourcePlugin", FilmSourcePlugin),
    ("FilmSourceProvider", FilmSourceProvider),
    ("HlsLocator", HlsLocator),
    ("HlsPlaybackTarget", HlsPlaybackTarget),
    ("HlsPlaylist", HlsPlaylist),
    ("HttpClient", HttpClient),
    ("Language", Language),
    ("MediaMetadata", MediaMetadata),
    ("PlaybackMediaFile", PlaybackMediaFile),
    ("PlaybackOffer", PlaybackOffer),
    ("PlaybackPlugin", PlaybackPlugin),
    ("PlaybackProvider", PlaybackProvider),
    ("PlaybackQuery", PlaybackQuery),
    ("PluginContext", PluginContext),
    ("RelativeHlsLocator", RelativeHlsLocator),
    ("RequestError", RequestError),
    ("SearchQuery", SearchQuery),
    ("SourceCapability", SourceCapability),
    ("SourceDescriptor", SourceDescriptor),
    ("SourceError", SourceError),
    ("SourceId", SourceId),
    ("SubtitleTrack", SubtitleTrack),
    ("UpstreamPlaybackTarget", UpstreamPlaybackTarget),
    ("WebClient", WebClient),
    ("film_actors", film_actors),
    ("is_external_source_id", is_external_source_id),
    ("validate_external_source_id", validate_external_source_id),
)


@pytest.mark.parametrize("value", ["2230", "a.b-c_d", "A1", "x" * 64])
def test_path_segment_ids_accept_path_safe_values(value: str) -> None:
    """流标识与轨道 id 进路径, 首字符必须是字母或数字."""
    assert PlaybackOffer(key=value, name="n", content_type="video/mp4").key == value
    assert SubtitleTrack(id=value, label="l").id == value


@pytest.mark.parametrize("value", ["", ".", "..", ".hidden", "../up", "a/b", "a b", "x" * 65])
def test_path_segment_ids_reject_unsafe_values(value: str) -> None:
    """``.`` 与 ``..`` 会被浏览器与服务器归一化, 选中这条流的意图随之丢失, 必须在契约层拒绝."""
    with pytest.raises(ValidationError):
        PlaybackOffer(key=value, name="n", content_type="video/mp4")
    with pytest.raises(ValidationError):
        SubtitleTrack(id=value, label="l")


def test_playback_offer_availability_is_derived_from_reason() -> None:
    """只有「可播」与「列出来并说明原因」两种状态, 没有「不可播但没原因」这种组合."""
    assert PlaybackOffer(key="k", name="n", content_type="video/mp4").unavailable is None
    assert PlaybackOffer(key="k", name="n", content_type="video/mp4", unavailable="文件为空").unavailable == "文件为空"
    with pytest.raises(ValidationError):
        PlaybackOffer(key="k", name="n", content_type="video/mp4", unavailable="")


def test_sdk_all_matches_reexport_table() -> None:
    assert sdk.__all__ == [name for name, _ in _REEXPORTS]


def test_sdk_reexports_are_host_objects() -> None:
    for name, expected in _REEXPORTS:
        assert getattr(sdk, name) is expected


def test_sdk_hides_host_runtime() -> None:
    for name in ("PluginManager", "PluginOrigin", "PluginConfig", "PluginLoadFailure"):
        assert name not in sdk.__all__
        assert not hasattr(sdk, name)
