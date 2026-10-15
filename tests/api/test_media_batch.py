"""/media/batch/delete HTTP 接线: 计数口径与单条批量删除."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestMediaBatchDelete:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_counts_and_missing(self, client: AsyncClient, seed_library: Repository) -> None:
        first = await seed_library.create_media_file(library_id=1, path="/video/A-1.mp4")
        second = await seed_library.create_media_file(library_id=1, path="/video/B-2.mp4")
        assert first.id is not None and second.id is not None

        resp = await client.post("media/batch/delete", json={"ids": [first.id, second.id, 999_999, second.id]})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2, "missing": 1}
        assert await seed_library.get_media_file(first.id) is None
        assert await seed_library.get_media_file(second.id) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_invalid_id_lists(self, client: AsyncClient) -> None:
        """空列表与超上限都是 422."""
        for ids in ([], list(range(1001))):
            assert (await client.post("media/batch/delete", json={"ids": ids})).status_code == 422
