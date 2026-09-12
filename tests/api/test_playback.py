"""Playback HTTP: upstream streams, plugin sources, route identity."""

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


async def _seed_title(repo: Repository, *, number: str = "PLAY-001") -> int:
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")
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
        self._send(with_body=True)

    def do_HEAD(self) -> None:
        self._send(with_body=False)

    def _send(self, *, with_body: bool) -> None:
        item = self.origin_files.get(urlparse(self.path).path)
        if item is None:
            self.send_error(404)
            return
        media_type, body = item
        header = self.headers.get("Range")
        content_range: str | None = None
        status = 200
        payload = body
        if header is not None and header.startswith("bytes="):
            first, _, last = header.removeprefix("bytes=").partition("-")
            start = int(first)
            if start >= len(body):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            stop = min(int(last) if last else len(body) - 1, len(body) - 1)
            status, payload = 206, body[start : stop + 1]
            content_range = f"bytes {start}-{stop}/{len(body)}"
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(payload)))
        if content_range is not None:
            self.send_header("Content-Range", content_range)
        self.send_header("Authorization", "Bearer leaked")
        self.end_headers()
        if with_body:
            self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


_BROKEN_PLUGIN = """
from pydantic import BaseModel

from amane.plugin import (
    PlaybackPlugin,
    PlaybackProvider,
    PluginContext,
    SourceCapability,
    SourceDescriptor,
)


class Plugin(PlaybackPlugin):
    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="acme.broken",
            name="Broken playback",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.PLAYBACK}),
            urls=("https://play.example.test",),
        )

    def build_playback(self, context: PluginContext, config: BaseModel) -> PlaybackProvider:
        raise RuntimeError("插件构造失败")
"""

# 探测调用次数落在插件自己的运行数据目录, 断言不依赖主机内部状态.
_COUNTING_PLUGIN = """
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from amane.plugin import (
    PlaybackOffer,
    PlaybackPlugin,
    PlaybackProvider,
    PlaybackQuery,
    PluginContext,
    SourceCapability,
    SourceDescriptor,
)

COUNT_FILE = "probe-count.txt"


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Provider(PlaybackProvider):
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def _record_probe(self) -> None:
        path = self._data_dir / COUNT_FILE
        count = int(path.read_text(encoding="utf-8")) if path.exists() else 0
        path.write_text(str(count + 1), encoding="utf-8")

    async def probe(self, query: PlaybackQuery) -> PlaybackOffer | None:
        self._record_probe()
        return None

    async def resolve(self, query: PlaybackQuery) -> None:
        return None


class Plugin(PlaybackPlugin):
    config_model = _Config

    @classmethod
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="acme.count",
            name="Counting playback",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.PLAYBACK}),
            urls=("https://play.example.test",),
        )

    def build_playback(self, context: PluginContext, config: BaseModel) -> PlaybackProvider:
        return _Provider(context.data_dir)
"""


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
    async def test_plugin_stream_range_and_route_identity(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        """码流端点转发单段 Range; 列表 ETag 与路由身份沿用同一契约."""
        payload = bytes(range(256)) * 4
        server, origin = _start_hls_origin({"/clip.mp4": ("video/mp4", payload)})
        try:
            data_dir = app.state.runtime.config.cold.data_dir
            write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
            reloaded = await client.post("plugins/reload")
            assert reloaded.status_code == 200, reloaded.text
            meta_id = await _seed_title(repo)
            configured = await client.patch(
                "plugins/acme.play",
                json={"enabled": True, "config": {"behavior": "upstream", "url": f"{origin}/clip.mp4"}},
            )
            assert configured.status_code == 200, configured.text

            listed = await client.get("playback/sources", params={"metadata_id": meta_id})
            assert listed.status_code == 200
            items = listed.json()["items"]
            assert [(row["source_id"], row["available"]) for row in items] == [("acme.play", True)]
            assert items[0]["media_file_id"] is None
            assert items[0]["href"] == f"/api/playback/acme.play/{meta_id}"
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

            stream = f"playback/acme.play/{meta_id}"
            full = await client.get(stream)
            assert full.status_code == 200
            assert full.content == payload
            assert "attachment" not in full.headers.get("content-disposition", "").casefold()
            assert full.headers.get("content-type", "").startswith("video/")
            assert full.headers.get("x-content-type-options") == "nosniff"
            assert "authorization" not in {key.casefold() for key in full.headers}

            head = await client.head(stream)
            assert head.status_code == 200
            assert head.content == b""

            partial = await client.get(stream, headers={"Range": "bytes=0-9"})
            assert partial.status_code == 206
            assert partial.headers["content-range"].startswith("bytes 0-9/")
            assert partial.content == payload[:10]

            unsatisfiable = await client.get(stream, headers={"Range": "bytes=999999-"})
            assert unsatisfiable.status_code == 416

            multi = await client.get(stream, headers={"Range": "bytes=0-1,2-3"})
            assert multi.status_code == 400

            assert (await client.get(f"playback/acme.play/{meta_id}/files/999999")).status_code == 404
            assert (await client.get("playback/sources", params={"metadata_id": 999999})).status_code == 404
            assert (await client.get(f"playback/missing.play/{meta_id}")).status_code == 404
            assert (await client.get(f"playback/{'a' * (SOURCE_ID_MAX_LEN + 1)}/{meta_id}")).status_code == 404
            assert (await client.get(f"playback/NOTVALID/{meta_id}")).status_code == 404
            assert (await client.get("playback/sources")).status_code == 422
            assert (await client.get("playback/sources", params={"metadata_id": 0})).status_code == 422
        finally:
            server.shutdown()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_plugin_file_target_streams_indexed_file(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
        safe_path: Path,
        tmp_path: Path,
    ) -> None:
        """插件声明条目索引内的文件时主机自行输出, 索引外的路径一律拒绝.

        符号链接指向库外 (收件目录) 照常可播: 主机打开的就是索引里的那条字面路径.
        """
        lib_root = safe_path / "lib"
        inbox = tmp_path / "inbox"
        lib_root.mkdir()
        inbox.mkdir()
        library = await repo.create_library(name="files", path=str(lib_root))
        assert library.id is not None

        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text

        payload = bytes(range(256)) * 4
        meta_id = await repo.upsert_metadata(number="PLAY-FILE", title="Play")
        assert meta_id.id is not None
        real = inbox / "clip.mp4"
        real.write_bytes(payload)
        alias = lib_root / "alias.mp4"
        alias.symlink_to(real)
        media = await repo.create_media_file(
            library_id=library.id,
            path=str(alias),
            size=len(payload),
            metadata_id=meta_id.id,
        )
        assert media.id is not None
        configured = await client.patch(
            "plugins/acme.play",
            json={"enabled": True, "config": {"behavior": "file"}},
        )
        assert configured.status_code == 200, configured.text

        listed = await client.get("playback/sources", params={"metadata_id": meta_id.id})
        assert listed.status_code == 200
        assert [(row["source_id"], row["available"]) for row in listed.json()["items"]] == [("acme.play", True)]

        stream = f"playback/acme.play/{meta_id.id}"
        full = await client.get(stream)
        assert full.status_code == 200
        assert full.content == payload
        assert full.headers.get("content-type", "").startswith("video/")
        assert full.headers.get("x-content-type-options") == "nosniff"
        assert "attachment" not in full.headers.get("content-disposition", "").casefold()

        head = await client.head(stream)
        assert head.status_code == 200
        assert head.content == b""

        partial = await client.get(stream, headers={"Range": "bytes=0-9"})
        assert partial.status_code == 206
        assert partial.headers["content-range"].startswith("bytes 0-9/")
        assert partial.content == payload[:10]

        unsatisfiable = await client.get(stream, headers={"Range": "bytes=999999-"})
        assert unsatisfiable.status_code == 416
        assert unsatisfiable.headers["content-range"] == f"bytes */{len(payload)}"

        foreign_meta = await repo.upsert_metadata(number="PLAY-FILE-FOREIGN", title="Play")
        assert foreign_meta.id is not None
        foreign_path = tmp_path / "foreign.mp4"
        await _attach_file(repo, foreign_meta.id, foreign_path, payload=b"foreign-bytes")

        configured = await client.patch(
            "plugins/acme.play",
            json={"enabled": True, "config": {"behavior": "file", "file_path": str(foreign_path)}},
        )
        assert configured.status_code == 200, configured.text
        # 同一路径在所属条目下可以播放, 在其它条目下属于插件侧错误.
        own = await client.get(f"playback/acme.play/{foreign_meta.id}")
        assert own.status_code == 200
        assert own.content == b"foreign-bytes"
        foreign_entry = await client.get(stream)
        assert foreign_entry.status_code == 502
        assert "索引" in str(foreign_entry.json().get("detail"))

        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"outside-bytes")
        configured = await client.patch(
            "plugins/acme.play",
            json={"enabled": True, "config": {"behavior": "file", "file_path": str(outside)}},
        )
        assert configured.status_code == 200, configured.text
        not_indexed = await client.get(stream)
        assert not_indexed.status_code == 502
        assert "索引" in str(not_indexed.json().get("detail"))
        assert b"outside-bytes" not in not_indexed.content

        configured = await client.patch(
            "plugins/acme.play",
            json={"enabled": True, "config": {"behavior": "file", "file_path": str(alias)}},
        )
        assert configured.status_code == 200, configured.text
        real.unlink()
        gone = await client.get(stream)
        assert gone.status_code == 502
        assert "不存在" in str(gone.json().get("detail"))

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
        assert [(row["source_id"], row["available"], row["detail"]) for row in none_list.json()["items"]] == [
            ("acme.play", False, None)
        ]
        assert (await client.get(f"playback/acme.play/{none_id}")).status_code == 404

        error_status, error_body = await play("error", "PLAY-ERR")
        assert error_status == 502
        assert error_body.get("detail") == "上游失败"

        error_id = await _seed_title(repo, number="PLAY-ERR-LIST")
        error_list = await client.get("playback/sources", params={"metadata_id": error_id})
        assert [(row["source_id"], row["available"], row["detail"]) for row in error_list.json()["items"]] == [
            ("acme.play", False, "上游失败")
        ]
        assert (await client.get(f"playback/acme.play/{error_id}")).status_code == 502

        hls_status, _hls_body = await play(
            "hls",
            "PLAY-HLS",
            playlist="#EXTM3U\n#EXT-X-ENDLIST\n",
        )
        assert hls_status == 200

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
    async def test_plugin_build_failure_is_502_not_500(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        """插件构造期异常归 502 (插件侧失败), 未启用与未安装仍是 404.

        ``build_playback`` 抛错时异常不得冒到路由变成 500; 三个调用点 (码流 / 清单 / 字幕) 共用
        ``PlaybackFactory.provider``, 返回值与错误语义必须一致.
        """
        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.broken", body=_BROKEN_PLUGIN)
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text
        metadata_id = await _seed_title(repo, number="PLAY-BUILD")

        assert (await client.get(f"playback/acme.missing/{metadata_id}")).status_code == 404
        disabled = await client.patch("plugins/acme.broken", json={"enabled": False, "config": {}})
        assert disabled.status_code == 200, disabled.text
        assert (await client.get(f"playback/acme.broken/{metadata_id}")).status_code == 404

        enabled = await client.patch("plugins/acme.broken", json={"enabled": True, "config": {}})
        assert enabled.status_code == 200, enabled.text
        for path in (
            f"playback/acme.broken/{metadata_id}",
            f"playback/acme.broken/{metadata_id}/index.m3u8",
            f"playback/acme.broken/{metadata_id}/subtitles/vtt",
        ):
            response = await client.get(path)
            assert response.status_code == 502, path
            assert response.json()["detail"] == "构建播放源失败"

        listed = await client.get("playback/sources", params={"metadata_id": metadata_id})
        assert listed.status_code == 200
        rows = listed.json()["items"]
        assert [row["source_id"] for row in rows] == ["acme.broken"]
        assert all(row["available"] is False for row in rows)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_probe_none_is_negatively_cached(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
    ) -> None:
        """``probe`` 返回 ``None`` 单独缓存: 同一条目再次探测不再调用插件的 ``probe``."""
        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.count", body=_COUNTING_PLUGIN)
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text
        metadata_id = await _seed_title(repo, number="PLAY-NONE-CACHE")
        enabled = await client.patch("plugins/acme.count", json={"enabled": True, "config": {}})
        assert enabled.status_code == 200, enabled.text
        counter = data_dir / "plugins" / "acme.count" / "probe-count.txt"

        first = await client.get("playback/sources", params={"metadata_id": metadata_id})
        assert first.status_code == 200
        assert [(row["source_id"], row["available"]) for row in first.json()["items"]] == [("acme.count", False)]
        assert counter.read_text(encoding="utf-8") == "1"

        second = await client.get("playback/sources", params={"metadata_id": metadata_id})
        assert second.status_code == 200
        assert second.json()["items"] == first.json()["items"]
        assert counter.read_text(encoding="utf-8") == "1"

        other_id = await _seed_title(repo, number="PLAY-NONE-CACHE-OTHER")
        third = await client.get("playback/sources", params={"metadata_id": other_id})
        assert third.status_code == 200
        assert counter.read_text(encoding="utf-8") == "2"

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
    async def test_hls_bad_uri_fails_only_that_uri(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
        safe_path: Path,
    ) -> None:
        """清单里一条无法定位的 URI 只作废自己: 其余分片照常可播.

        无法定位的 URI 仍然改写到本机 (上游 Origin 不得因此漏进清单), 请求它的 token 时返回 502
        与原始原因; 归属校验不变, 其它条目 / 文件 / 来源请求同一个 token 一律 404.
        """
        server, origin = _start_hls_origin({"/good.ts": ("video/mp4", b"GOODSEG")})
        try:
            data_dir = app.state.runtime.config.cold.data_dir
            write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
            reloaded = await client.post("plugins/reload")
            assert reloaded.status_code == 200, reloaded.text
            metadata_id = await _seed_title(repo, number="PLAY-HLS-BAD")
            playlist = (
                "#EXTM3U\n"
                '#EXT-X-MAP:URI="//evil.example/init.mp4"\n'
                "#EXTINF:1.0,\n"
                "//evil.example/seg.ts\n"
                "#EXTINF:1.0,\n"
                "file:///etc/passwd\n"
                "#EXTINF:1.0,\n"
                "http://[::1\n"
                "#EXTINF:1.0,\n"
                "good.ts\n"
                "#EXT-X-ENDLIST\n"
            )
            configured = await client.patch(
                "plugins/acme.play",
                json={
                    "enabled": True,
                    "config": {"behavior": "hls", "url": f"{origin}/index.m3u8", "playlist": playlist},
                },
            )
            assert configured.status_code == 200, configured.text

            manifest = await client.get(f"playback/acme.play/{metadata_id}/index.m3u8")
            assert manifest.status_code == 200
            assert "evil.example" not in manifest.text
            assert "/etc/passwd" not in manifest.text
            assert "[::1" not in manifest.text
            map_token, cross_token, scheme_token, malformed_token, good_token = _hls_part_tokens(manifest.text)

            for token, detail in (
                (map_token, "播放列表 URI 跨源"),
                (cross_token, "播放列表 URI 跨源"),
                (scheme_token, "播放列表 URI 不受支持"),
                (malformed_token, "播放列表 URI 不受支持"),
            ):
                broken = await client.get(f"playback/acme.play/{metadata_id}/hls/{token}")
                assert broken.status_code == 502
                assert broken.json()["detail"] == detail
                assert "evil.example" not in broken.text

            segment = await client.get(f"playback/acme.play/{metadata_id}/hls/{good_token}")
            assert segment.status_code == 200
            assert segment.content == b"GOODSEG"

            other_id = await _seed_title(repo, number="PLAY-HLS-BAD-OTHER")
            file_id = await _attach_file(repo, metadata_id, safe_path / "clip.mp4", payload=b"video-bytes")
            write_plugin(data_dir, "acme.other", body=playback_plugin_source("acme.other"))
            assert (await client.post("plugins/reload")).status_code == 200
            enabled_other = await client.patch("plugins/acme.other", json={"enabled": True, "config": {}})
            assert enabled_other.status_code == 200, enabled_other.text
            foreign_entry = await client.get(f"playback/acme.play/{other_id}/hls/{cross_token}")
            assert foreign_entry.status_code == 404
            foreign_file = await client.get(f"playback/acme.play/{metadata_id}/files/{file_id}/hls/{cross_token}")
            assert foreign_file.status_code == 404
            foreign_source = await client.get(f"playback/acme.other/{metadata_id}/hls/{cross_token}")
            assert foreign_source.status_code == 404
        finally:
            server.shutdown()

    @pytest.mark.parametrize(
        ("tag", "uri", "detail"),
        [
            ("EXT-X-KEY", "//evil.example/enc.key", "播放列表 URI 跨源"),
            ("EXT-X-KEY", "file:///etc/passwd", "播放列表 URI 不受支持"),
            ("EXT-X-SESSION-KEY", "//evil.example/enc.key", "播放列表 URI 跨源"),
        ],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_hls_bad_key_uri_fails_whole_playlist(
        self,
        client: AsyncClient,
        repo: Repository,
        app: FastAPI,
        tag: str,
        uri: str,
        detail: str,
    ) -> None:
        """密钥 URI 无法定位时整份清单直接 502: 缺密钥整份都播不了, 提前给出原因比逐个分片失败更利于排查."""
        data_dir = app.state.runtime.config.cold.data_dir
        write_plugin(data_dir, "acme.play", body=playback_plugin_source("acme.play"))
        reloaded = await client.post("plugins/reload")
        assert reloaded.status_code == 200, reloaded.text
        metadata_id = await _seed_title(repo, number="PLAY-HLS-KEY")
        playlist = f'#EXTM3U\n#{tag}:METHOD=AES-128,URI="{uri}"\n#EXTINF:1.0,\nseg.ts\n#EXT-X-ENDLIST\n'
        configured = await client.patch(
            "plugins/acme.play",
            json={
                "enabled": True,
                "config": {"behavior": "hls", "url": "http://cdn.example/index.m3u8", "playlist": playlist},
            },
        )
        assert configured.status_code == 200, configured.text
        manifest = await client.get(f"playback/acme.play/{metadata_id}/index.m3u8")
        assert manifest.status_code == 502
        assert manifest.json()["detail"] == detail
        assert "evil.example" not in manifest.text
