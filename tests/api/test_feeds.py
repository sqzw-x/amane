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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_items_include_metadata_id(self, client: AsyncClient, repo: Repository):
        created = (await client.post("feeds", json=_body())).json()
        feed_id = created["id"]
        await repo.create_feed_item(feed_id, "k-hit", title="MIDV-123", number="MIDV-123")
        await repo.create_feed_item(feed_id, "k-miss", title="none")
        meta = await repo.upsert_metadata(number="MIDV-123")
        assert meta.id is not None
        items = (await client.get(f"feeds/{feed_id}/items")).json()["items"]
        by_key = {item["item_key"]: item for item in items}
        assert by_key["k-hit"]["metadata_id"] == meta.id
        assert by_key["k-miss"]["metadata_id"] is None
        assert by_key["k-miss"]["number"] is None
        assert by_key["k-hit"]["ignored_at"] is None
        assert by_key["k-hit"]["published_at"] is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feed_item_search_and_state_filter(self, client: AsyncClient, repo: Repository):
        feed = await repo.create_feed(name="search", url="https://example.com/search-items.xml")
        assert feed.id is not None
        active = await repo.create_feed_item(feed.id, "active-key", title="Find me", number="AAA-001")
        ignored = await repo.create_feed_item(feed.id, "ignored-key", title="Skip me", number="BBB-002")
        assert active.id is not None and ignored.id is not None
        await repo.ignore_feed_items(feed.id, [ignored.id])

        default_items = await client.get(f"feeds/{feed.id}/items")
        assert default_items.status_code == 200
        assert [item["item_key"] for item in default_items.json()["items"]] == ["active-key"]

        ignored_items = await client.get(f"feeds/{feed.id}/items", params={"state": "ignored"})
        assert ignored_items.status_code == 200
        assert [item["item_key"] for item in ignored_items.json()["items"]] == ["ignored-key"]
        assert ignored_items.json()["items"][0]["ignored_at"] is not None

        searched = await client.get(f"feeds/{feed.id}/items", params={"state": "all", "search": "AAA"})
        assert searched.status_code == 200
        assert [item["item_key"] for item in searched.json()["items"]] == ["active-key"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feed_item_batch_actions(self, client: AsyncClient, repo: Repository):
        feed = await repo.create_feed(name="actions", url="https://example.com/action-items.xml")
        other = await repo.create_feed(name="other", url="https://example.com/other-items.xml")
        assert feed.id is not None and other.id is not None
        first = await repo.create_feed_item(feed.id, "first")
        second = await repo.create_feed_item(feed.id, "second")
        foreign = await repo.create_feed_item(other.id, "foreign")
        assert first.id is not None and second.id is not None and foreign.id is not None

        ignored = await client.post(
            f"feeds/{feed.id}/items/batch", json={"action": "ignore", "ids": [first.id, first.id, foreign.id, 9999]}
        )
        assert ignored.status_code == 200
        assert ignored.json() == {"affected": 1, "missing": 2}

        restored = await client.post(
            f"feeds/{feed.id}/items/batch", json={"action": "unignore", "ids": [first.id, second.id]}
        )
        assert restored.status_code == 200
        assert restored.json() == {"affected": 2, "missing": 0}

        deleted = await client.post(
            f"feeds/{feed.id}/items/batch", json={"action": "delete", "ids": [second.id, foreign.id, 9999]}
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"affected": 1, "missing": 2}

        remaining = await client.get(f"feeds/{feed.id}/items", params={"state": "all"})
        assert [item["item_key"] for item in remaining.json()["items"]] == ["first"]
        other_items = await client.get(f"feeds/{other.id}/items", params={"state": "all"})
        assert [item["item_key"] for item in other_items.json()["items"]] == ["foreign"]

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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feed_item_batch_scrape_reports_empty_submission(self, client: AsyncClient, repo: Repository):
        feed = await repo.create_feed(name="scrape-empty", url="https://example.com/scrape-empty.xml")
        assert feed.id is not None
        item = await repo.create_feed_item(feed.id, "no-number")
        assert item.id is not None

        response = await client.post(f"feeds/{feed.id}/items/batch", json={"action": "scrape", "ids": [item.id]})

        assert response.status_code == 200
        assert response.json() == {
            "affected": 1,
            "missing": 0,
            "skipped": 1,
            "submitted": 0,
            "task_ids": [],
        }
        assert await repo.list_tasks() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_feed_item_batch_validation(self, client: AsyncClient):
        created = (await client.post("feeds", json=_body(url="https://example.com/validation.xml"))).json()
        feed_id = created["id"]

        empty_ids = await client.post(f"feeds/{feed_id}/items/batch", json={"action": "ignore", "ids": []})
        assert empty_ids.status_code == 422

        invalid_action = await client.post(f"feeds/{feed_id}/items/batch", json={"action": "unknown", "ids": [1]})
        assert invalid_action.status_code == 422

        missing_feed = await client.post("feeds/9999/items/batch", json={"action": "ignore", "ids": [1]})
        assert missing_feed.status_code == 404


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
    async def test_list_all_items_filters_by_feed_and_group(self, client: AsyncClient, repo: Repository):
        jav = await repo.create_feed(name="jav", url="https://example.com/jav.xml", group="jav/rsshub")
        news = await repo.create_feed(name="news", url="https://example.com/news.xml", group="news")
        bare = await repo.create_feed(name="bare", url="https://example.com/bare.xml")
        assert jav.id is not None and news.id is not None and bare.id is not None
        await repo.create_feed_item(jav.id, "j1", title="Jav item", description="<p>hello</p>")
        await repo.create_feed_item(news.id, "n1", title="News item")
        await repo.create_feed_item(bare.id, "b1", title="Bare item")

        all_items = await client.get("feeds/items", params={"state": "all"})
        assert all_items.status_code == 200
        assert all_items.json()["total"] == 3
        assert {item["item_key"] for item in all_items.json()["items"]} == {"j1", "n1", "b1"}
        jav_item = next(item for item in all_items.json()["items"] if item["item_key"] == "j1")
        assert jav_item["description"] == "<p>hello</p>"

        by_feed = await client.get("feeds/items", params={"state": "all", "feed_id": jav.id})
        assert [item["item_key"] for item in by_feed.json()["items"]] == ["j1"]

        by_group = await client.get("feeds/items", params={"state": "all", "group": "jav"})
        assert [item["item_key"] for item in by_group.json()["items"]] == ["j1"]

        ungrouped = await client.get("feeds/items", params={"state": "all", "group": ""})
        assert [item["item_key"] for item in ungrouped.json()["items"]] == ["b1"]

        missing = await client.get("feeds/items", params={"feed_id": 9999})
        assert missing.status_code == 404
