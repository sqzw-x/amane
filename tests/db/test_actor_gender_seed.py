"""影片刮削写入时填 Actor.gender 空位, 不覆盖已有性别."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from amane.enums import ActorGender

if TYPE_CHECKING:
    from amane.db.repository import Repository

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("desc", "writes", "lookup", "expect_name", "expect_gender"),
    [
        (
            "新建填空",
            [("GD-1", ["NewFemale"], {"NewFemale": ActorGender.FEMALE})],
            "NewFemale",
            "NewFemale",
            ActorGender.FEMALE,
        ),
        (
            "已有性别不覆盖",
            [
                ("GD-1", ["X"], {"X": ActorGender.MALE}),
                ("GD-2", ["X"], {"X": ActorGender.FEMALE}),
            ],
            "X",
            "X",
            ActorGender.MALE,
        ),
        (
            "别名形式按解析后展示名填空",
            [("GD-4", ["Display(AliasOne)"], {"Display(AliasOne)": ActorGender.FEMALE})],
            "Display",
            "Display",
            ActorGender.FEMALE,
        ),
    ],
    ids=["seed", "keep_known", "alias"],
)
async def test_upsert_seeds_actor_gender(
    repo: Repository,
    desc: str,
    writes: list[tuple[str, list[str], dict[str, ActorGender]]],
    lookup: str,
    expect_name: str,
    expect_gender: ActorGender,
) -> None:
    for number, actors, genders in writes:
        await repo.upsert_metadata(number=number, actors=actors, actor_genders=genders)
    actor = (await repo.get_actors_by_names([lookup]))[0]
    assert actor.name == expect_name, desc
    assert actor.gender is expect_gender, desc
