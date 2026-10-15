"""/actors/batch/* HTTP 接线: 性别批量写入与人物档案批量清空."""

from typing import TYPE_CHECKING

import pytest

from amane.db.models import FacetKind

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


async def _seed_actors(repo: Repository) -> dict[str, int]:
    await repo.upsert_metadata(number="ACT-BATCH-1", actors=["BatchA", "BatchB"])
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    return {a.name: a.id for a in actors if a.name.startswith("Batch") and a.id is not None}


class TestActorBatchGender:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_writes_gender_and_locks_fields(self, client: AsyncClient, seed_library: Repository) -> None:
        ids = await _seed_actors(seed_library)
        resp = await client.post(
            "actors/batch/gender",
            json={"ids": [ids["BatchA"], ids["BatchB"], ids["BatchA"], 999_999], "gender": "female"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"updated": 2, "missing": 1}

        for name in ("BatchA", "BatchB"):
            actor = await seed_library.get_actor(ids[name])
            assert actor is not None
            assert actor.gender == "female"
            assert actor.locked_fields == ["gender"]


class TestActorBatchClearPerson:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_clears_person_and_keeps_name(self, client: AsyncClient, seed_library: Repository) -> None:
        ids = await _seed_actors(seed_library)
        actor = await seed_library.get_actor(ids["BatchA"])
        assert actor is not None
        actor.birthday = "1991-01-01"
        actor.raw = {"minnano": {"birthday": "1991-01-01"}}
        actor.locked_fields = ["birthday"]
        await seed_library.save_actor(actor, aliases=["Nick"])

        resp = await client.post("actors/batch/clear-person", json={"ids": [ids["BatchA"], 999_999]})
        assert resp.status_code == 200
        assert resp.json() == {"cleared": 1, "missing": 1}

        cleared = await seed_library.get_actor(ids["BatchA"])
        assert cleared is not None
        assert cleared.name == "BatchA"
        assert cleared.gender == "unknown"
        assert cleared.birthday is None
        assert cleared.raw == {}
        assert cleared.locked_fields == []
        assert await seed_library.get_actor_aliases(ids["BatchA"]) == []


class TestActorBatchValidation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_invalid_bodies(self, client: AsyncClient) -> None:
        """空 id 列表 / 非法性别 / 超上限都是 422."""
        cases = (
            ("actors/batch/gender", {"ids": [], "gender": "female"}),
            ("actors/batch/gender", {"ids": [1], "gender": "bogus"}),
            ("actors/batch/clear-person", {"ids": []}),
            ("actors/batch/clear-person", {"ids": list(range(1001))}),
        )
        for path, body in cases:
            assert (await client.post(path, json=body)).status_code == 422, path
