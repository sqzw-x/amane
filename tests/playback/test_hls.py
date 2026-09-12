"""HLS playlist rewrite: URI lines, attribute URIs, no upstream origin leakage."""

from collections.abc import Callable

import pytest

from amane.playback.hls import rewrite_playlist, should_map_uri


def _map(uri: str) -> str:
    return f"/api/playback/acme.play/1/hls/{uri.replace('/', '_')}"


@pytest.mark.parametrize(
    ("source", "check"),
    [
        (
            "#EXTM3U\n#EXTINF:1.0,\nseg.ts\n#EXT-X-ENDLIST\n",
            lambda out: out.splitlines()[2] == "/api/playback/acme.play/1/hls/seg.ts",
        ),
        (
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="enc.key"\n#EXTINF:1,\nseg.ts\n',
            lambda out: 'URI="/api/playback/acme.play/1/hls/enc.key"' in out,
        ),
        (
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nvariant.m3u8\n",
            lambda out: "/api/playback/acme.play/1/hls/variant.m3u8" in out,
        ),
        (
            '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\nseg.m4s\n',
            lambda out: 'URI="/api/playback/acme.play/1/hls/init.mp4"' in out,
        ),
        (
            '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="eng",URI="audio.m3u8"\n',
            lambda out: 'URI="/api/playback/acme.play/1/hls/audio.m3u8"' in out,
        ),
        (
            "#EXTM3U\n#EXTINF:1.0,\n#EXT-X-BYTERANGE:1000@0\nseg.ts\n",
            lambda out: "/api/playback/acme.play/1/hls/seg.ts" in out and "#EXT-X-BYTERANGE:1000@0" in out,
        ),
        (
            '#EXTM3U\n#EXTINF:1,\ndata:application/octet-stream,AAAA\n#EXT-X-KEY:METHOD=NONE,URI=""\n',
            lambda out: "data:application/octet-stream,AAAA" in out,
        ),
    ],
)
def test_rewrite_playlist_maps_uris(source: str, check: Callable[[str], bool]) -> None:
    rewritten = rewrite_playlist(source, _map)
    assert "http://" not in rewritten
    assert check(rewritten)


def test_rewrite_playlist_does_not_map_host_paths() -> None:
    source = "#EXTM3U\n#EXTINF:1,\n/api/playback/acme.play/1/hls/already\n"
    assert rewrite_playlist(source, _map) == source


def test_rewrite_playlist_strips_bom_and_crlf() -> None:
    source = "\ufeff#EXTM3U\r\n#EXTINF:1.0,\r\nseg.ts\r\n"
    rewritten = rewrite_playlist(source, _map)
    assert rewritten.startswith("#EXTM3U\n")
    assert "\r" not in rewritten
    assert "/api/playback/acme.play/1/hls/seg.ts" in rewritten


def test_should_map_uri() -> None:
    assert should_map_uri("seg.ts") is True
    assert should_map_uri("https://cdn.example/seg.ts") is True
    assert should_map_uri("/api/playback/x/1/hls/ab") is False
    assert should_map_uri("data:text/plain,x") is False
    assert should_map_uri("") is False


def test_rewrite_playlist_strips_upstream_origin() -> None:
    source = "#EXTM3U\n#EXTINF:1.0,\nhttp://cdn.example/path/seg.ts\n"
    rewritten = rewrite_playlist(source, lambda _uri: "/api/playback/acme.play/1/hls/deadbeef")
    assert "cdn.example" not in rewritten
    assert rewritten.splitlines()[2] == "/api/playback/acme.play/1/hls/deadbeef"
