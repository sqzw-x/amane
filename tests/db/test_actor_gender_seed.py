"""影片刮削写入时填 Actor.gender 空位, 不覆盖已有性别."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.enums import ActorGender

if TYPE_CHECKING:
    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio


async def test_upsert_seeds_unknown_gender(repo: Repository) -> None:
    await repo.upsert_metadata(
        number="GD-1",
        actors=["NewFemale"],
        actor_genders={"NewFemale": ActorGender.FEMALE},
    )
    actor = (await repo.get_actors_by_names(["NewFemale"]))[0]
    assert actor.gender is ActorGender.FEMALE


async def test_upsert_does_not_overwrite_known_gender(repo: Repository) -> None:
    await repo.upsert_metadata(number="GD-1", actors=["X"], actor_genders={"X": ActorGender.MALE})
    await repo.upsert_metadata(number="GD-2", actors=["X"], actor_genders={"X": ActorGender.FEMALE})
    actor = (await repo.get_actors_by_names(["X"]))[0]
    assert actor.gender is ActorGender.MALE


async def test_upsert_without_genders_leaves_unknown(repo: Repository) -> None:
    await repo.upsert_metadata(number="GD-3", actors=["Plain"])
    actor = (await repo.get_actors_by_names(["Plain"]))[0]
    assert actor.gender is ActorGender.UNKNOWN


async def test_alias_form_seeds_resolved_actor(repo: Repository) -> None:
    await repo.upsert_metadata(
        number="GD-4",
        actors=["Display(AliasOne)"],
        actor_genders={"Display(AliasOne)": ActorGender.FEMALE},
    )
    actor = (await repo.get_actors_by_names(["Display"]))[0]
    assert actor.gender is ActorGender.FEMALE
    assert actor.name == "Display"
