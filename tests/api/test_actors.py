"""演员浏览 API 测试."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.db.models import FacetKind
from amane.enums import ActorGender

if TYPE_CHECKING:
    from httpx2 import AsyncClient

    from amane.db.repository import Repository


class TestActorsApi:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_detail_scrape_and_filters(
        self, client: AsyncClient, repo: Repository, stop_worker: None
    ) -> None:
        await repo.upsert_metadata(number="ACT-BR-1", actors=["EmptyOne", "FilledOne"])
        actors, _ = await repo.list_facets(FacetKind.ACTOR)
        empty_id = next(a.id for a in actors if a.name == "EmptyOne")
        filled_id = next(a.id for a in actors if a.name == "FilledOne")
        assert empty_id is not None and filled_id is not None

        filled = await repo.get_actor(filled_id)
        assert filled is not None
        filled.birthday = "1991-01-01"
        filled.height = 160
        filled.image_urls = ["https://img.example/a.jpg"]
        filled.overview = "bio-not-for-list"
        await repo.save_actor(filled, aliases=["HiddenFromList"])

        resp = await client.get("actors")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        names = {i["name"] for i in body["items"]}
        assert "EmptyOne" in names and "FilledOne" in names
        listed = next(i for i in body["items"] if i["id"] == filled_id)
        assert listed["overview"] is None
        assert listed["aliases"] == []
        assert listed["image_urls"] == ["https://img.example/a.jpg"]

        no_person = await client.get("actors", params={"has_person": "false"})
        assert no_person.status_code == 200
        assert all(i["name"] != "FilledOne" or not i.get("birthday") for i in no_person.json()["items"])
        assert any(i["id"] == empty_id for i in no_person.json()["items"])

        with_image = await client.get("actors", params={"has_image": "true"})
        assert with_image.status_code == 200
        assert any(i["id"] == filled_id for i in with_image.json()["items"])
        assert all(i["image_urls"] for i in with_image.json()["items"])

        detail = await client.get(f"actors/{filled_id}")
        assert detail.status_code == 200
        assert detail.json()["birthday"] == "1991-01-01"
        assert detail.json()["gender"] == "unknown"
        assert detail.json()["count"] >= 1
        assert detail.json()["overview"] == "bio-not-for-list"
        assert detail.json()["aliases"] == ["HiddenFromList"]
        assert "raw" in detail.json()

        patched = await client.patch(
            f"actors/{filled_id}",
            json={
                "overview": "edited bio",
                "gender": "female",
                "image_urls": ["https://img.example/b.jpg", "https://img.example/a.jpg"],
                "birthday": None,
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["overview"] == "edited bio"
        assert body["gender"] == "female"
        assert body["image_urls"][0] == "https://img.example/b.jpg"
        assert body["birthday"] is None

        empty_patch = await client.patch(f"actors/{filled_id}", json={})
        assert empty_patch.status_code == 422

        scrape = await client.post(f"actors/{filled_id}/scrape")
        assert scrape.status_code == 202
        assert scrape.json()["type"] == "actor_scrape"
        assert scrape.json()["payload"]["actor_id"] == filled_id
        assert set(scrape.json()["payload"]["use_cache"]) == {"metadata", "trans"}

        force = await client.post(f"actors/{filled_id}/scrape", json={"use_cache": []})
        assert force.status_code == 202
        assert force.json()["id"] == scrape.json()["id"]

        missing = await client.get("actors/99999")
        assert missing.status_code == 404

        missing_patch = await client.patch("actors/99999", json={"overview": "x"})
        assert missing_patch.status_code == 404

        bad_bday = await client.patch(f"actors/{filled_id}", json={"birthday": "not-a-date"})
        assert bad_bday.status_code == 422

        norm_bday = await client.patch(f"actors/{filled_id}", json={"birthday": "1991年1月1日"})
        assert norm_bday.status_code == 200
        assert norm_bday.json()["birthday"] == "1991-01-01"

        by_bday = await client.get("actors", params={"sort_by": "birthday", "order": "asc", "limit": 100})
        assert by_bday.status_code == 200
        bdays = [i.get("birthday") for i in by_bday.json()["items"] if i.get("birthday")]
        assert bdays == sorted(bdays)

        by_height = await client.get("actors", params={"sort_by": "height", "order": "desc"})
        assert by_height.status_code == 200


async def _seed_filter_actors(repo: Repository) -> dict[str, int]:
    """准备字段筛选用例演员, 返回 name→id."""
    await repo.upsert_metadata(
        number="ACT-FLT-1", actors=["FltTall", "FltShort", "FltNullMetric", "FltAliasOnly", "FltMale"]
    )
    actors, _ = await repo.list_facets(FacetKind.ACTOR)
    by_name = {a.name: a.id for a in actors if a.id is not None and a.name.startswith("Flt")}

    tall = await repo.get_actor(by_name["FltTall"])
    assert tall is not None
    tall.gender = ActorGender.FEMALE
    tall.birthday = "1990-06-15"
    tall.height = 168
    tall.bust = 86
    tall.waist = 58
    tall.hip = 88
    tall.cup = "D"
    tall.birthplace = "Tokyo"
    tall.image_urls = ["https://img.example/tall.jpg"]
    await repo.save_actor(tall, aliases=["高子"])

    short = await repo.get_actor(by_name["FltShort"])
    assert short is not None
    short.gender = ActorGender.FEMALE
    short.birthday = "1995-01-01"
    short.height = 150
    short.bust = 80
    short.waist = 55
    short.hip = 82
    short.cup = "B"
    short.birthplace = "Osaka"
    await repo.save_actor(short)

    # 无身高/生日, 仅 gender known - 范围筛选应排除
    null_metric = await repo.get_actor(by_name["FltNullMetric"])
    assert null_metric is not None
    null_metric.gender = ActorGender.FEMALE
    await repo.save_actor(null_metric)

    alias_only = await repo.get_actor(by_name["FltAliasOnly"])
    assert alias_only is not None
    await repo.save_actor(alias_only, aliases=["HiddenAliasXYZ"])

    male = await repo.get_actor(by_name["FltMale"])
    assert male is not None
    male.gender = ActorGender.MALE
    male.height = 175
    male.birthday = "1988-12-01"
    await repo.save_actor(male)

    return {k: v for k, v in by_name.items() if v is not None}


# (query params, 期望 Flt* 名, HTTP status). 同一 client/播种上循环, 避免 parametrize 付 N 次 lifespan.
_FILTER_CASES: list[tuple[dict[str, str | int | list[str]], set[str], int]] = [
    ({"gender": "female"}, {"FltTall", "FltShort", "FltNullMetric"}, 200),
    ({"gender": ["female", "male"]}, {"FltTall", "FltShort", "FltNullMetric", "FltMale"}, 200),
    ({"height_min": 160}, {"FltTall", "FltMale"}, 200),
    ({"height_max": 155}, {"FltShort"}, 200),
    ({"height_min": 155, "height_max": 170}, {"FltTall"}, 200),
    ({"birthday_min": "1992-01-01"}, {"FltShort"}, 200),
    ({"birthday_max": "1991-12-31"}, {"FltTall", "FltMale"}, 200),
    ({"birthday_min": "1990-01-01", "birthday_max": "1990-12-31"}, {"FltTall"}, 200),
    ({"cup_min": "C", "cup_max": "E"}, {"FltTall"}, 200),
    ({"cup_max": "B"}, {"FltShort"}, 200),
    ({"bust_min": 85}, {"FltTall"}, 200),
    ({"birthplace": "kyo"}, {"FltTall"}, 200),
    ({"height_min": 160, "has_image": "true"}, {"FltTall"}, 200),
    ({"height_min": 200, "height_max": 150}, set(), 422),
    ({"birthday_min": "1995-01-01", "birthday_max": "1990-01-01"}, set(), 422),
    ({"cup_min": "E", "cup_max": "A"}, set(), 422),
    ({"birthday_min": "not-a-date"}, set(), 422),
    ({"birthday_max": "1990/13/40"}, set(), 422),
]


class TestActorFieldFilters:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_field_filters(self, client: AsyncClient, repo: Repository) -> None:
        ids = await _seed_filter_actors(repo)

        alias = await client.get("actors", params={"search": "HiddenAliasXYZ"})
        assert alias.status_code == 200
        found = {i["id"] for i in alias.json()["items"]}
        assert ids["FltAliasOnly"] in found
        assert ids["FltTall"] not in found

        for params, expect_names, status in _FILTER_CASES:
            resp = await client.get("actors", params={**params, "limit": 200})
            assert resp.status_code == status, params
            if status != 200:
                continue
            got = {i["name"] for i in resp.json()["items"] if i["name"].startswith("Flt")}
            assert got == expect_names, params
            if "height_min" in params or "height_max" in params:
                assert ids["FltNullMetric"] not in {i["id"] for i in resp.json()["items"]}
