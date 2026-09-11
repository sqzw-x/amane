"""/feeds 端点测试"""

from typing import TYPE_CHECKING

import pytest

from amane.api.models.feeds import normalize_feed_group
from amane.db.repository import Repository
from amane.parsing import ContentType

if TYPE_CHECKING:
    from httpx2 import AsyncClient


def _body(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "javdb",
        "url": "https://example.com/rss.xml",
    }
    data.update(overrides)
    return data


class TestFeedsCrud:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_get(self, client: AsyncClient):
        empty = await client.get("feeds")
        assert empty.status_code == 200
        assert empty.json() == {"items": [], "total": 0}

        resp = await client.post("feeds", json=_body())
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "javdb"
        assert data["url"] == "https://example.com/rss.xml"
        assert data["enabled"] is True
        assert data["auto_enqueue"] is True
        assert data["interval_seconds"] == 3600
        assert set(data["use_cache"]) == {"metadata", "trans"}
        assert data["content_type"] is None
        assert data["group"] == ""
        off = await client.post("feeds", json=_body(url="https://example.com/manual.xml", auto_enqueue=False))
        assert off.status_code == 201
        assert off.json()["auto_enqueue"] is False
        fetched = await client.get(f"feeds/{data['id']}")
        assert fetched.status_code == 200
        missing = await client.get("feeds/9999")
        assert missing.status_code == 404

        assert (await client.post("feeds", json=_body(name="other"))).status_code == 409
        blank = await client.post("feeds", json=_body(url="https://example.com/blank.xml", name="   "))
        assert blank.status_code == 201
        assert blank.json()["name"] == ""
        omitted = await client.post("feeds", json={"url": "https://example.com/other.xml"})
        assert omitted.status_code == 201
        assert omitted.json()["name"] == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_invalid_payloads(self, client: AsyncClient):
        assert (await client.post("feeds", json=_body(url="ftp://example.com/rss"))).status_code == 422
        assert (await client.post("feeds", json=_body(interval_seconds=59))).status_code == 422
        assert (
            await client.post("feeds", json=_body(url="https://example.com/b.xml", interval_seconds=86401))
        ).status_code == 422
        assert (await client.post("feeds", json=_body(number_pattern="("))).status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_patch_and_delete(self, client: AsyncClient):
        created = (await client.post("feeds", json=_body())).json()
        feed_id = created["id"]
        patched = await client.patch(
            f"feeds/{feed_id}",
            json={"interval_seconds": 600, "enabled": False, "auto_enqueue": False, "content_type": "fc2"},
        )
        assert patched.status_code == 200
        data = patched.json()
        assert data["interval_seconds"] == 600
        assert data["enabled"] is False
        assert data["auto_enqueue"] is False
        assert data["content_type"] == "fc2"

        cache = await client.patch(f"feeds/{feed_id}", json={"use_cache": ["trans"]})
        assert cache.status_code == 200
        assert cache.json()["use_cache"] == ["trans"]
        empty_cache = await client.patch(f"feeds/{feed_id}", json={"use_cache": []})
        assert empty_cache.status_code == 200
        assert empty_cache.json()["use_cache"] == []

        bad_interval = await client.patch(f"feeds/{feed_id}", json={"interval_seconds": 1})
        assert bad_interval.status_code == 422

        deleted = await client.delete(f"feeds/{feed_id}")
        assert deleted.status_code == 204
        assert (await client.get(f"feeds/{feed_id}")).status_code == 404
        assert (await client.delete("feeds/9999")).status_code == 404

    @pytest.mark.asyncio(loop_scope="function")
    async def test_poll_and_items(self, client: AsyncClient, repo: Repository):
        created = (await client.post("feeds", json=_body())).json()
        feed_id = created["id"]
        polled = await client.post(f"feeds/{feed_id}/poll")
        assert polled.status_code == 202
        assert polled.json()["last_error"] == "test stub"

        items = await client.get(f"feeds/{feed_id}/items")
        assert items.status_code == 200
        assert items.json()["items"] == []
        assert items.json()["total"] == 0

        missing = await client.post("feeds/9999/poll")
        assert missing.status_code == 404
        assert (await client.get("feeds/9999/items")).status_code == 404

        await repo.create_feed_item(feed_id, "k-hit", title="MIDV-123", number="MIDV-123")
        await repo.create_feed_item(feed_id, "k-miss", title="none")
        meta = await repo.upsert_metadata(number="MIDV-123")
        assert meta.id is not None
        listed = (await client.get(f"feeds/{feed_id}/items", params={"state": "all"})).json()["items"]
        by_key = {item["item_key"]: item for item in listed}
        assert by_key["k-hit"]["metadata_id"] == meta.id
        assert by_key["k-miss"]["metadata_id"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feed_item_batch_scrape_uses_feed_config(self, client: AsyncClient, repo: Repository):
        feed = await repo.create_feed(
            name="scrape",
            url="https://example.com/scrape-items.xml",
            content_type=ContentType.WESTERN,
            use_cache=[],
        )
        other = await repo.create_feed(name="other", url="https://example.com/other-scrape.xml")
        assert feed.id is not None and other.id is not None
        first = await repo.create_feed_item(feed.id, "first", number="ABC-001")
        duplicate = await repo.create_feed_item(feed.id, "duplicate", number="abc-001")
        no_number = await repo.create_feed_item(feed.id, "no-number")
        foreign = await repo.create_feed_item(other.id, "foreign", number="XYZ-999")
        assert first.id is not None and duplicate.id is not None
        assert no_number.id is not None and foreign.id is not None

        response = await client.post(
            f"feeds/{feed.id}/items/batch",
            json={"action": "scrape", "ids": [first.id, first.id, duplicate.id, no_number.id, foreign.id]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["affected"] == 3
        assert body["missing"] == 1
        assert body["skipped"] == 1
        assert body["submitted"] == 1
        assert len(body["task_ids"]) == 1

        tasks = await repo.list_tasks()
        assert len(tasks) == 1
        task = tasks[0]
        assert task.priority == 0
        assert task.payload == {
            "number": "ABC-001",
            "content_type": "western",
            "media_file_id": None,
            "use_cache": [],
        }

        assert (
            await client.post(f"feeds/{feed.id}/items/batch", json={"action": "ignore", "ids": []})
        ).status_code == 422
        assert (
            await client.post(f"feeds/{feed.id}/items/batch", json={"action": "unknown", "ids": [1]})
        ).status_code == 422
        assert (await client.post("feeds/9999/items/batch", json={"action": "ignore", "ids": [1]})).status_code == 404


class TestFeedGroup:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            ("  ", ""),
            ("jav", "jav"),
            ("/jav/", "jav"),
            ("jav//rsshub", "jav/rsshub"),
            (" jav / rsshub ", "jav/rsshub"),
            ("a\\b", "a/b"),
        ],
    )
    def test_normalize_feed_group(self, raw: str | None, expected: str):
        assert normalize_feed_group(raw) == expected

    @pytest.mark.parametrize("raw", [".", "..", "a/../b", "a/./b", "x\ny"])
    def test_normalize_feed_group_rejects(self, raw: str):
        with pytest.raises(ValueError):
            normalize_feed_group(raw)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_create_and_patch_group(self, client: AsyncClient):
        created = await client.post("feeds", json=_body(group=" jav // rsshub /"))
        assert created.status_code == 201
        assert created.json()["group"] == "jav/rsshub"

        feed_id = created.json()["id"]
        patched = await client.patch(f"feeds/{feed_id}", json={"group": "news"})
        assert patched.status_code == 200
        assert patched.json()["group"] == "news"

        cleared = await client.patch(f"feeds/{feed_id}", json={"group": "  "})
        assert cleared.status_code == 200
        assert cleared.json()["group"] == ""

        bad = await client.patch(f"feeds/{feed_id}", json={"group": "a/../b"})
        assert bad.status_code == 422

        invalid_create = await client.post("feeds", json=_body(url="https://example.com/dots.xml", group=".."))
        assert invalid_create.status_code == 422

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_all_items_http(self, client: AsyncClient, repo: Repository):
        jav = await repo.create_feed(name="jav", url="https://example.com/jav.xml", group="jav/rsshub")
        assert jav.id is not None
        await repo.create_feed_item(jav.id, "j1", title="Jav item", description="<p>hello</p>")
        listed = await client.get("feeds/items", params={"state": "all"})
        assert listed.status_code == 200
        jav_item = next(item for item in listed.json()["items"] if item["item_key"] == "j1")
        assert jav_item["description"] == "<p>hello</p>"
        assert jav_item["read_at"] is None
        marked = await client.post(f"feeds/{jav.id}/items/batch", json={"action": "read", "ids": [jav_item["id"]]})
        assert marked.status_code == 200
        assert marked.json()["affected"] == 1
        unread = await client.get("feeds/items", params={"state": "all", "read": "unread"})
        assert unread.status_code == 200
        assert all(item["item_key"] != "j1" for item in unread.json()["items"])
        seen = await client.get("feeds/items", params={"state": "all", "read": "read"})
        assert seen.status_code == 200
        read_item = next(item for item in seen.json()["items"] if item["item_key"] == "j1")
        assert read_item["read_at"] is not None
        restored = await client.post(f"feeds/{jav.id}/items/batch", json={"action": "unread", "ids": [jav_item["id"]]})
        assert restored.status_code == 200
        assert (await client.get("feeds/items", params={"read": "nope"})).status_code == 422
        assert (await client.get("feeds/items", params={"feed_id": 9999})).status_code == 404
