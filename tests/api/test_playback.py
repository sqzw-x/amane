"""Playback HTTP: Range, local files, plugin resolve, route identity."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

import pytest

from amane.playback.factory import SOURCE_ID_MAX_LEN
from tests.plugins.test_plugin_system import playback_plugin_source, write_plugin

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


async def _seed_title(repo: Repository, *, number: str = "PLAY-001", library_path: str = "/") -> int:
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path=library_path)
    meta = await repo.upsert_metadata(number=number, title="Play")
    assert meta.id is not None
    return meta.id


async def _attach_file(
    repo: Repository,
    metadata_id: int,
    path: Path,
    *,
    payload: bytes,
) -> int:
    path.write_bytes(payload)
    media = await repo.create_media_file(
        library_id=1,
        path=str(path.resolve()),
        size=len(payload),
        metadata_id=metadata_id,
    )
    assert media.id is not None
    return media.id


def _start_hls_origin(files: dict[str, tuple[str, bytes]]) -> tuple[ThreadingHTTPServer, str]:
    handler = type("HlsOrigin", (_HlsOriginHandler,), {"origin_files": files})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    return server, f"http://{host}:{port}"


class _HlsOriginHandler(BaseHTTPRequestHandler):
    origin_files: ClassVar[dict[str, tuple[str, bytes]]] = {}

    def do_GET(self) -> None:
        item = self.origin_files.get(urlparse(self.path).path)
        if item is None:
            self.send_error(404)
            return
        media_type, body = item
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Authorization", "Bearer leaked")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _hls_part_tokens(playlist: str) -> list[str]:
    tokens: list[str] = []
    needle = "/hls/"
    start = 0
    while True:
        index = playlist.find(needle, start)
        if index < 0:
            break
        token = playlist[index + len(needle) : index + len(needle) + 32]
        tokens.append(token)
        start = index + len(needle) + 32
    return tokens


class TestPlaybackHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_range_identity_and_errors(
        self,
        client: AsyncClient,
        repo: Repository,
        safe_path: Path,
    ) -> None:
        meta_id = await _seed_title(repo, library_path=str(safe_path))
        payload = bytes(range(256)) * 4
        file_id = await _attach_file(repo, meta_id, safe_path / "clip.mp4", payload=payload)

        listed = await client.get("playback/sources", params={"metadata_id": meta_id})
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["source_id"] == "local"
        assert items[0]["media_file_id"] == file_id
        assert items[0]["href"] == f"/api/playback/local/{meta_id}/files/{file_id}"
        etag = listed.headers["etag"]
        assert listed.headers["cache-control"] == "private, no-cache"
        for validator in (etag, f"W/{etag}", f'"other", {etag}', "*"):
            cached = await client.get(
                "playback/sources",
                params={"metadata_id": meta_id},
                headers={"If-None-Match": validator},
            )
            assert cached.status_code == 304
            assert cached.headers["cache-control"] == "private, no-cache"

        stream = f"playback/local/{meta_id}"
        full = await client.get(stream)
        assert full.status_code == 200
        assert full.content == payload
        assert "attachment" not in full.headers.get("content-disposition", "").casefold()
        assert full.headers.get("content-type", "").startswith("video/")
        assert full.headers.get("x-content-type-options") == "nosniff"
        assert "private" in full.headers.get("cache-control", "").casefold()

        head = await client.head(stream)
        assert head.status_code == 200
        assert head.content == b""
        assert "attachment" not in head.headers.get("content-disposition", "").casefold()

        partial = await client.get(stream, headers={"Range": "bytes=0-9"})
        assert partial.status_code == 206
        assert partial.headers["content-range"].startswith("bytes 0-9/")
        assert partial.content == payload[:10]

        unsatisfiable = await client.get(stream, headers={"Range": "bytes=999999-"})
        assert unsatisfiable.status_code == 416
        assert unsatisfiable.headers["content-range"] == f"bytes */{len(payload)}"

        malformed = await client.get(stream, headers={"Range": "bytes=abc"})
        assert malformed.status_code == 400

        ignored = await client.get(
            stream,
            headers={"Range": "bytes=0-9", "If-Range": '"not-the-etag"'},
        )
        assert ignored.status_code == 200
        assert ignored.content == payload

        other = await _seed_title(repo, number="PLAY-002")
        foreign = await _attach_file(repo, other, safe_path / "other.mp4", payload=b"xxxx")
        assert (await client.get(f"playback/local/{meta_id}/files/{foreign}")).status_code == 404
        assert (await client.get(f"playback/local/{meta_id}/files/999999")).status_code == 404
        assert (await client.get("playback/sources", params={"metadata_id": 999999})).status_code == 404
        assert (await client.get(f"playback/missing.play/{meta_id}")).status_code == 404
        assert (await client.get(f"playback/{'a' * (SOURCE_ID_MAX_LEN + 1)}/{meta_id}")).status_code == 404
        assert (await client.get(f"playback/NOTVALID/{meta_id}")).status_code == 404
        assert (await client.get("playback/sources")).status_code == 422
        assert (await client.get("playback/sources", params={"metadata_id": 0})).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_skips_unreadable_and_picks_default(
        self,
        client: AsyncClient,
        repo: Repository,
        safe_path: Path,
        tmp_path: Path,
    ) -> None:
        empty_id = await _seed_title(repo, number="PLAY-EMPTY", library_path=str(safe_path))
        empty = await client.get("playback/sources", params={"metadata_id": empty_id})
        assert empty.status_code == 200
        assert [(row["source_id"], row["available"]) for row in empty.json()["items"]] == [("local", False)]
        assert (await client.get(f"playback/local/{empty_id}")).status_code == 404

        strm_id = await _seed_title(repo, number="PLAY-STRM")
        strm = await _attach_file(repo, strm_id, safe_path / "clip.strm", payload=b"http://example.test/a.mp4")
        strm_list = await client.get("playback/sources", params={"metadata_id": strm_id})
        assert [(row["source_id"], row["available"]) for row in strm_list.json()["items"]] == [("local", False)]
        assert (await client.get(f"playback/local/{strm_id}/files/{strm}")).status_code == 404

        missing_id = await _seed_title(repo, number="PLAY-GONE")
        missing = await repo.create_media_file(
            library_id=1,
            path=str((safe_path / "gone.mp4").resolve()),
            size=10,
            metadata_id=missing_id,
        )
        assert missing.id is not None
        gone = await client.get("playback/sources", params={"metadata_id": missing_id})
        assert [(row["source_id"], row["available"]) for row in gone.json()["items"]] == [("local", False)]

        outside_id = await _seed_title(repo, number="PLAY-OUT")
        outside = tmp_path / "outside.mp4"
        outside_file = await _attach_file(repo, outside_id, outside, payload=b"outside-bytes")
        outside_list = await client.get("playback/sources", params={"metadata_id": outside_id})
        assert [(row["source_id"], row["available"]) for row in outside_list.json()["items"]] == [("local", False)]
        assert (await client.get(f"playback/local/{outside_id}/files/{outside_file}")).status_code == 404

        multi_id = await _seed_title(repo, number="PLAY-MULTI")
        small = await _attach_file(repo, multi_id, safe_path / "small.mp4", payload=b"s" * 20)
        large = await _attach_file(repo, multi_id, safe_path / "large.mp4", payload=b"L" * 80)
        picked = await client.get("playback/sources", params={"metadata_id": multi_id})
        assert picked.json()["items"][0]["media_file_id"] == large
        default_body = await client.get(f"playback/local/{multi_id}")
        assert default_body.content == b"L" * 80
        explicit = await client.get(f"playback/local/{multi_id}/files/{small}")
        assert explicit.status_code == 200
        assert explicit.content == b"s" * 20

        linked_id = await _seed_title(repo, number="PLAY-LINK")
        real = safe_path / "real.mp4"
        real.write_bytes(b"linked-bytes")
        alias = safe_path / "alias.mp4"
        alias.symlink_to(real)
        linked_file = await repo.create_media_file(
            library_id=1,
            path=str(alias),
            size=len(b"linked-bytes"),
            metadata_id=linked_id,
        )
        assert linked_file.id is not None
        linked_list = await client.get("playback/sources", params={"metadata_id": linked_id})
        assert linked_list.json()["items"][0]["media_file_id"] == linked_file.id
        linked_body = await client.get(f"playback/local/{linked_id}/files/{linked_file.id}")
        assert linked_body.status_code == 200
        assert linked_body.content == b"linked-bytes"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_playback_plugin_resolve_and_routes(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text

        listed = await client.get("plugins")
        assert "acme.play" in {item["descriptor"]["id"] for item in listed.json()["items"]}

        async def play(behavior: str, number: str, **extra: object) -> tuple[int, dict[str, object]]:
            metadata_id = await _seed_title(repo, number=number)
            body: dict[str, object] = {"behavior": behavior, **extra}
            configured = await client.patch("plugins/acme.play", json={"enabled": True, "config": body})
            assert configured.status_code == 200, configured.text
            response = await client.get(f"playback/acme.play/{metadata_id}")
            return response.status_code, response.json() if response.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}

        none_id = await _seed_title(repo, number="PLAY-NONE")
        none_cfg = await client.patch("plugins/acme.play", json={"enabled": True, "config": {"behavior": "none"}})
        assert none_cfg.status_code == 200, none_cfg.text
        none_list = await client.get("playback/sources", params={"metadata_id": none_id})
        assert [(row["source_id"], row["available"]) for row in none_list.json()["items"]] == [
            ("local", False),
            ("acme.play", False),
        ]
        assert (await client.get(f"playback/acme.play/{none_id}")).status_code == 404

        error_status, error_body = await play("error", "PLAY-ERR")
        assert error_status == 502
        assert error_body.get("detail") == "上游失败"

        hls_status, _hls_body = await play(
            "hls",
            "PLAY-HLS",
            playlist="#EXTM3U\n#EXT-X-ENDLIST\n",
        )
        assert hls_status == 200

        file_status, file_body = await play("file", "PLAY-FILE")
        assert file_status == 502
        assert "本地文件" in str(file_body.get("detail"))

        lie_id = await _seed_title(repo, number="PLAY-HLS-LIE")
        lie_cfg = await client.patch(
            "plugins/acme.play",
            json={"enabled": True, "config": {"behavior": "hls-offer", "url": "http://127.0.0.1:9/video"}},
        )
        assert lie_cfg.status_code == 200, lie_cfg.text
        lie_list = await client.get("playback/sources", params={"metadata_id": lie_id})
        lie_item = next(row for row in lie_list.json()["items"] if row["source_id"] == "acme.play")
        assert lie_item["href"] == f"/api/playback/acme.play/{lie_id}/index.m3u8"
        lie = await client.get(f"playback/acme.play/{lie_id}/index.m3u8")
        assert lie.status_code == 502
        assert lie.json()["detail"] == "不是 HLS 播放源"

        routes = await client.patch(
            "config",
            json={"scraping": {"content_routes": {"censored": ["acme.play"]}}},
        )
        assert routes.status_code == 422

        disabled = await client.patch("plugins/acme.play", json={"enabled": False, "config": {}})
        assert disabled.status_code == 200
        assert (await client.get(f"playback/acme.play/{none_id}")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_hls_playlist_rewrite_and_parts(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        server, origin = _start_hls_origin(
            {
                "/seg.ts": ("video/mp4", b"SEGMENTDATA"),
                "/enc.key": ("application/octet-stream", b"KEYBYTES"),
                "/child.m3u8": (
                    "application/vnd.apple.mpegurl",
                    b"#EXTM3U\n#EXTINF:1.0,\nseg.ts\n#EXT-X-ENDLIST\n",
                ),
            }
        )
        try:
            data_dir = app.state.runtime.config.cold.data_dir
            write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
            reloaded = await client.post("plugins/reload")
            assert reloaded.status_code == 200, reloaded.text
            metadata_id = await _seed_title(repo, number="PLAY-HLS-FULL")
            playlist = (
                "#EXTM3U\n"
                "#EXT-X-VERSION:3\n"
                '#EXT-X-KEY:METHOD=AES-128,URI="enc.key"\n'
                "#EXTINF:1.0,\n"
                "seg.ts\n"
                "#EXT-X-STREAM-INF:BANDWIDTH=800000\n"
                "child.m3u8\n"
            )
            configured = await client.patch(
                "plugins/acme.play",
                json={
                    "enabled": True,
                    "config": {
                        "behavior": "hls",
                        "url": f"{origin}/index.m3u8",
                        "headers": {"Authorization": "Bearer secret"},
                        "playlist": playlist,
                    },
                },
            )
            assert configured.status_code == 200, configured.text

            listed = await client.get("playback/sources", params={"metadata_id": metadata_id})
            assert listed.status_code == 200
            item = next(row for row in listed.json()["items"] if row["source_id"] == "acme.play")
            assert item["href"] == f"/api/playback/acme.play/{metadata_id}/index.m3u8"
            assert item["content_type"] == "application/vnd.apple.mpegurl"

            manifest = await client.get(f"playback/acme.play/{metadata_id}/index.m3u8")
            assert manifest.status_code == 200
            text = manifest.text
            assert origin not in text
            assert "enc.key" not in text
            assert "\nseg.ts\n" not in text
            assert "child.m3u8" not in text
            assert "private" in manifest.headers.get("cache-control", "").casefold()
            assert "public" not in manifest.headers.get("cache-control", "").casefold()
            assert "authorization" not in {key.casefold() for key in manifest.headers}

            tokens = _hls_part_tokens(text)
            assert len(tokens) == 3
            key_token, segment_token, child_token = tokens
            segment = await client.get(f"playback/acme.play/{metadata_id}/hls/{segment_token}")
            assert segment.status_code == 200
            assert segment.content == b"SEGMENTDATA"
            cache = segment.headers.get("cache-control", "").casefold()
            assert "private" in cache
            assert "immutable" in cache
            assert "public" not in cache
            assert "authorization" not in {key.casefold() for key in segment.headers}
            assert segment.headers.get("x-content-type-options") == "nosniff"

            key = await client.get(f"playback/acme.play/{metadata_id}/hls/{key_token}")
            assert key.status_code == 200
            assert key.content == b"KEYBYTES"

            child = await client.get(f"playback/acme.play/{metadata_id}/hls/{child_token}")
            assert child.status_code == 200
            assert origin not in child.text
            child_tokens = _hls_part_tokens(child.text)
            assert child_tokens[0] == segment_token
            nested = await client.get(f"playback/acme.play/{metadata_id}/hls/{child_tokens[0]}")
            assert nested.status_code == 200
            assert nested.content == b"SEGMENTDATA"

            missing = await client.get(f"playback/acme.play/{metadata_id}/hls/{'a' * 32}")
            assert missing.status_code == 404
        finally:
            server.shutdown()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_sidecar_subtitle(
        self,
        client: AsyncClient,
        repo: Repository,
        safe_path: Path,
    ) -> None:
        meta_id = await _seed_title(repo, number="PLAY-SUB", library_path=str(safe_path))
        file_id = await _attach_file(repo, meta_id, safe_path / "clip.mp4", payload=b"video-bytes")
        (safe_path / "clip.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")

        listed = await client.get("playback/sources", params={"metadata_id": meta_id})
        item = listed.json()["items"][0]
        assert item["source_id"] == "local"
        assert len(item["subtitles"]) == 1
        track = item["subtitles"][0]
        assert track["id"] == "sidecar"
        assert track["href"] == f"/api/playback/local/{meta_id}/files/{file_id}/subtitles/sidecar"

        vtt = await client.get(f"playback/local/{meta_id}/files/{file_id}/subtitles/sidecar")
        assert vtt.status_code == 200
        assert vtt.headers.get("content-type", "").startswith("text/vtt")
        assert "WEBVTT" in vtt.text
        assert "00:00:01.000 --> 00:00:02.000" in vtt.text
        assert (await client.get(f"playback/local/{meta_id}/files/{file_id}/subtitles/missing")).status_code == 404

        linked_id = await _seed_title(repo, number="PLAY-SUB-LINK")
        real = safe_path / "real.mp4"
        real.write_bytes(b"linked-bytes")
        alias = safe_path / "alias.mp4"
        alias.symlink_to(real)
        (safe_path / "alias.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        linked_file = await repo.create_media_file(
            library_id=1,
            path=str(alias),
            size=len(b"linked-bytes"),
            metadata_id=linked_id,
        )
        assert linked_file.id is not None
        linked_list = await client.get("playback/sources", params={"metadata_id": linked_id})
        linked_item = linked_list.json()["items"][0]
        assert any(track["id"] == "sidecar" for track in linked_item["subtitles"])
        linked_vtt = await client.get(f"playback/local/{linked_id}/files/{linked_file.id}/subtitles/sidecar")
        assert linked_vtt.status_code == 200
        assert "WEBVTT" in linked_vtt.text

        escaped_id = await _seed_title(repo, number="PLAY-SUB-ESC")
        nested = safe_path / "nested"
        nested.mkdir()
        (nested / "leaked.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nNo\n", encoding="utf-8")
        clip = safe_path / "stay.mp4"
        clip.write_bytes(b"stay-bytes")
        leaked_link = safe_path / "stay.srt"
        leaked_link.symlink_to(nested / "leaked.srt")
        escaped_file = await repo.create_media_file(
            library_id=1,
            path=str(clip),
            size=len(b"stay-bytes"),
            metadata_id=escaped_id,
        )
        assert escaped_file.id is not None
        escaped_list = await client.get("playback/sources", params={"metadata_id": escaped_id})
        assert escaped_list.json()["items"][0]["subtitles"] == []
        assert (
            await client.get(f"playback/local/{escaped_id}/files/{escaped_file.id}/subtitles/sidecar")
        ).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_hls_nested_subdirectory_segments(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        child_playlist = b"#EXTM3U\n#EXTINF:1.0,\nseg.ts\n#EXT-X-ENDLIST\n"
        server, origin = _start_hls_origin(
            {
                "/video/480p/index.m3u8": ("application/vnd.apple.mpegurl", child_playlist),
                "/video/480p/seg.ts": ("video/mp4", b"NESTEDSEG"),
                "/video/seg.ts": ("video/mp4", b"WRONGDIR"),
            }
        )
        try:
            data_dir = app.state.runtime.config.cold.data_dir
            write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
            reloaded = await client.post("plugins/reload")
            assert reloaded.status_code == 200, reloaded.text
            metadata_id = await _seed_title(repo, number="PLAY-HLS-NEST")
            configured = await client.patch(
                "plugins/acme.play",
                json={
                    "enabled": True,
                    "config": {
                        "behavior": "hls",
                        "url": f"{origin}/video/master.m3u8",
                        "playlist": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n480p/index.m3u8\n",
                    },
                },
            )
            assert configured.status_code == 200, configured.text
            manifest = await client.get(f"playback/acme.play/{metadata_id}/index.m3u8")
            assert manifest.status_code == 200
            assert origin not in manifest.text
            child_token = _hls_part_tokens(manifest.text)[0]
            child = await client.get(f"playback/acme.play/{metadata_id}/hls/{child_token}")
            assert child.status_code == 200
            assert origin not in child.text
            seg_token = _hls_part_tokens(child.text)[0]
            segment = await client.get(f"playback/acme.play/{metadata_id}/hls/{seg_token}")
            assert segment.status_code == 200
            assert segment.content == b"NESTEDSEG"
        finally:
            server.shutdown()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_plugin_subtitle_text_and_upstream(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        server, origin = _start_hls_origin({"/sub.vtt": ("text/vtt", b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nUp\n")})
        html_server, html_origin = _start_hls_origin({"/sub.vtt": ("text/html", b"<html>no</html>")})
        try:
            data_dir = app.state.runtime.config.cold.data_dir
            write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
            reloaded = await client.post("plugins/reload")
            assert reloaded.status_code == 200, reloaded.text
            metadata_id = await _seed_title(repo, number="PLAY-SUB-PLUGIN")
            enabled = await client.patch(
                "plugins/acme.play",
                json={"enabled": True, "config": {"behavior": "upstream", "url": f"{origin}/sub.vtt"}},
            )
            assert enabled.status_code == 200, enabled.text
            inline = await client.get(f"playback/acme.play/{metadata_id}/subtitles/vtt")
            assert inline.status_code == 200
            assert inline.headers.get("content-type", "").startswith("text/vtt")
            assert "WEBVTT" in inline.text
            assert inline.headers.get("x-content-type-options") == "nosniff"
            assert (await client.get(f"playback/acme.play/{metadata_id}/subtitles/missing")).status_code == 404

            remote = await client.get(f"playback/acme.play/{metadata_id}/subtitles/remote")
            assert remote.status_code == 200
            assert b"WEBVTT" in remote.content

            html_cfg = await client.patch(
                "plugins/acme.play",
                json={"enabled": True, "config": {"behavior": "upstream", "url": f"{html_origin}/sub.vtt"}},
            )
            assert html_cfg.status_code == 200, html_cfg.text
            rejected = await client.get(f"playback/acme.play/{metadata_id}/subtitles/remote")
            assert rejected.status_code == 502
        finally:
            server.shutdown()
            html_server.shutdown()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_hls_rejects_protocol_relative_cross_origin(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text
        metadata_id = await _seed_title(repo, number="PLAY-HLS-PROTO")
        configured = await client.patch(
            "plugins/acme.play",
            json={
                "enabled": True,
                "config": {
                    "behavior": "hls",
                    "url": "http://cdn.example/index.m3u8",
                    "playlist": "#EXTM3U\n#EXTINF:1.0,\n//evil.example/seg.ts\n#EXT-X-ENDLIST\n",
                },
            },
        )
        assert configured.status_code == 200, configured.text
        manifest = await client.get(f"playback/acme.play/{metadata_id}/index.m3u8")
        assert manifest.status_code == 502
        assert "跨源" in str(manifest.json()["detail"])

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_symlink_to_inbox_inside_safe_dirs(
        self,
        client: AsyncClient,
        repo: Repository,
        safe_path: Path,
        tmp_path: Path,
    ) -> None:
        lib_root = safe_path / "lib"
        inbox = safe_path / "inbox"
        lib_root.mkdir()
        inbox.mkdir()
        library = await repo.create_library(name="sym", path=str(lib_root))
        assert library.id is not None

        meta = await repo.upsert_metadata(number="PLAY-INBOX", title="Play")
        assert meta.id is not None
        real = inbox / "clip.mp4"
        real.write_bytes(b"inbox-bytes")
        alias = lib_root / "alias.mp4"
        alias.symlink_to(real)
        (lib_root / "alias.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        media = await repo.create_media_file(
            library_id=library.id,
            path=str(alias),
            size=len(b"inbox-bytes"),
            metadata_id=meta.id,
        )
        assert media.id is not None
        listed = await client.get("playback/sources", params={"metadata_id": meta.id})
        item = listed.json()["items"][0]
        assert item["source_id"] == "local"
        assert any(track["id"] == "sidecar" for track in item["subtitles"])
        body = await client.get(f"playback/local/{meta.id}/files/{media.id}")
        assert body.status_code == 200
        assert body.content == b"inbox-bytes"
        vtt = await client.get(f"playback/local/{meta.id}/files/{media.id}/subtitles/sidecar")
        assert vtt.status_code == 200
        assert "WEBVTT" in vtt.text

        foreign_meta = await repo.upsert_metadata(number="PLAY-INBOX-OUT", title="Play")
        assert foreign_meta.id is not None
        foreign = tmp_path / "foreign.mp4"
        foreign.write_bytes(b"foreign-bytes")
        escape = lib_root / "escape.mp4"
        escape.symlink_to(foreign)
        escape_file = await repo.create_media_file(
            library_id=library.id,
            path=str(escape),
            size=len(b"foreign-bytes"),
            metadata_id=foreign_meta.id,
        )
        assert escape_file.id is not None
        foreign_list = await client.get("playback/sources", params={"metadata_id": foreign_meta.id})
        assert [(row["source_id"], row["available"]) for row in foreign_list.json()["items"]] == [("local", False)]
        assert (await client.get(f"playback/local/{foreign_meta.id}/files/{escape_file.id}")).status_code == 404

        stray_meta = await repo.upsert_metadata(number="PLAY-INBOX-STRAY", title="Play")
        assert stray_meta.id is not None
        stray = tmp_path / "stray.mp4"
        stray.write_bytes(b"stray-bytes")
        stray_file = await repo.create_media_file(
            library_id=library.id,
            path=str(stray),
            size=len(b"stray-bytes"),
            metadata_id=stray_meta.id,
        )
        assert stray_file.id is not None
        stray_list = await client.get("playback/sources", params={"metadata_id": stray_meta.id})
        assert [(row["source_id"], row["available"]) for row in stray_list.json()["items"]] == [("local", False)]
        assert (await client.get(f"playback/local/{stray_meta.id}/files/{stray_file.id}")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_allow_all_plays_library_symlink_to_inbox(
        self,
        allow_all_client: AsyncClient,
        allow_all_app: FastAPI,
        tmp_path: Path,
    ) -> None:
        repo = allow_all_app.state.runtime.repo
        library_root = tmp_path / "lib"
        library_root.mkdir()
        library = await repo.create_library(name="default", path=str(library_root))
        assert library.id is not None
        meta = await repo.upsert_metadata(number="PLAY-ALLOW", title="Play")
        assert meta.id is not None
        outside = tmp_path / "inbox" / "clip.mp4"
        outside.parent.mkdir()
        outside.write_bytes(b"inbox-bytes")
        alias = library_root / "alias.mp4"
        alias.symlink_to(outside)
        (library_root / "alias.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        media = await repo.create_media_file(
            library_id=library.id,
            path=str(alias),
            size=len(b"inbox-bytes"),
            metadata_id=meta.id,
        )
        assert media.id is not None
        listed = await allow_all_client.get("playback/sources", params={"metadata_id": meta.id})
        item = listed.json()["items"][0]
        assert item["source_id"] == "local"
        assert any(track["id"] == "sidecar" for track in item["subtitles"])
        streamed = await allow_all_client.get(f"playback/local/{meta.id}/files/{media.id}")
        assert streamed.status_code == 200
        assert streamed.content == b"inbox-bytes"
        vtt = await allow_all_client.get(f"playback/local/{meta.id}/files/{media.id}/subtitles/sidecar")
        assert vtt.status_code == 200
        assert "WEBVTT" in vtt.text

        stray_meta = await repo.upsert_metadata(number="PLAY-ALLOW-STRAY", title="Play")
        assert stray_meta.id is not None
        stray = tmp_path / "stray.mp4"
        stray.write_bytes(b"stray-bytes")
        stray_file = await repo.create_media_file(
            library_id=library.id,
            path=str(stray),
            size=len(b"stray-bytes"),
            metadata_id=stray_meta.id,
        )
        assert stray_file.id is not None
        stray_list = await allow_all_client.get("playback/sources", params={"metadata_id": stray_meta.id})
        assert [(row["source_id"], row["available"]) for row in stray_list.json()["items"]] == [("local", False)]
        assert (await allow_all_client.get(f"playback/local/{stray_meta.id}/files/{stray_file.id}")).status_code == 404
