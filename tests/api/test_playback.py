"""Playback HTTP: Range, local files, plugin resolve, route identity."""

from pathlib import Path
from typing import TYPE_CHECKING

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


class TestPlaybackHttp:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_range_identity_and_errors(
        self,
        client: AsyncClient,
        repo: Repository,
        safe_path: Path,
    ) -> None:
        meta_id = await _seed_title(repo)
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
        cached = await client.get(
            "playback/sources",
            params={"metadata_id": meta_id},
            headers={"If-None-Match": etag},
        )
        assert cached.status_code == 304

        stream = f"playback/local/{meta_id}"
        full = await client.get(stream)
        assert full.status_code == 200
        assert full.content == payload
        assert "attachment" not in full.headers.get("content-disposition", "").casefold()
        assert full.headers.get("content-type", "").startswith("video/")

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
        empty_id = await _seed_title(repo, number="PLAY-EMPTY")
        empty = await client.get("playback/sources", params={"metadata_id": empty_id})
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert (await client.get(f"playback/local/{empty_id}")).status_code == 404

        strm_id = await _seed_title(repo, number="PLAY-STRM")
        strm = await _attach_file(repo, strm_id, safe_path / "clip.strm", payload=b"http://example.test/a.mp4")
        strm_list = await client.get("playback/sources", params={"metadata_id": strm_id})
        assert strm_list.json()["items"] == []
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
        assert gone.json()["items"] == []

        outside_id = await _seed_title(repo, number="PLAY-OUT")
        outside = tmp_path / "outside.mp4"
        outside_file = await _attach_file(repo, outside_id, outside, payload=b"outside-bytes")
        outside_list = await client.get("playback/sources", params={"metadata_id": outside_id})
        assert outside_list.json()["items"] == []
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
        assert all(item["source_id"] != "acme.play" for item in none_list.json()["items"])
        assert (await client.get(f"playback/acme.play/{none_id}")).status_code == 404

        error_status, error_body = await play("error", "PLAY-ERR")
        assert error_status == 502
        assert error_body.get("detail") == "上游失败"

        hls_status, _hls_body = await play("hls", "PLAY-HLS")
        assert hls_status == 502

        file_status, file_body = await play("file", "PLAY-FILE")
        assert file_status == 502
        assert "本地文件" in str(file_body.get("detail"))

        routes = await client.patch(
            "config",
            json={"scraping": {"content_routes": {"censored": ["acme.play"]}}},
        )
        assert routes.status_code == 422

        disabled = await client.patch("plugins/acme.play", json={"enabled": False, "config": {}})
        assert disabled.status_code == 200
        assert (await client.get(f"playback/acme.play/{none_id}")).status_code == 404
