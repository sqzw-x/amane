"""CloudDrive webhook 端点."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.helpers import await_for

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestCloudDriveWebhook:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_requires_cloud_path(self, client: AsyncClient, safe_path: Path) -> None:
        target = safe_path / "cd"
        target.mkdir()
        missing = await client.post(
            "libraries",
            json={"path": str(target), "ingest": "clouddrive", "scan": False},
        )
        assert missing.status_code == 422

        created = await client.post(
            "libraries",
            json={
                "path": str(target),
                "ingest": "clouddrive",
                "cloud_path": "/115open/lib",
                "automation": "watch",
                "scan": False,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["ingest"] == "clouddrive"
        assert body["cloud_path"] == "/115open/lib"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_notify_file_create(self, client: AsyncClient, repo: Repository, safe_path: Path) -> None:
        target = safe_path / "wh"
        target.mkdir()
        video = target / "a.mp4"
        video.write_bytes(b"x" * 32)
        created = await client.post(
            "libraries",
            json={
                "path": str(target),
                "ingest": "clouddrive",
                "cloud_path": "/115open/lib",
                "automation": "watch",
                "scan": False,
            },
        )
        assert created.status_code == 201

        posted = await client.post(
            "webhooks/clouddrive",
            json={
                "data": [
                    {
                        "action": "create",
                        "is_dir": "false",
                        "source_file": "/115open/lib/a.mp4",
                        "destination_file": "",
                    }
                ]
            },
        )
        assert posted.status_code == 204

        async def found() -> bool:
            return await repo.get_media_file_by_path(str(video)) is not None

        await await_for(found)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unmatched_path_is_noop(self, client: AsyncClient, repo: Repository, safe_path: Path) -> None:
        target = safe_path / "wh2"
        target.mkdir()
        created = await client.post(
            "libraries",
            json={
                "path": str(target),
                "ingest": "clouddrive",
                "cloud_path": "/115open/lib",
                "automation": "watch",
                "scan": False,
            },
        )
        lib_id = created.json()["id"]
        posted = await client.post(
            "webhooks/clouddrive",
            json={"data": [{"action": "create", "is_dir": False, "source_file": "/115open/other/a.mp4"}]},
        )
        assert posted.status_code == 204
        assert await repo.list_media_files(library_id=lib_id, limit=None) == []
