"""FilmActor 收旧 list[str]; 名单语义写出的性别保留."""

import pytest
from pydantic import ValidationError

from amane.crawlers.models import FilmActor, MediaMetadata, film_actors
from amane.enums import ActorGender


def test_coerce_string_list_to_unknown_gender() -> None:
    meta = MediaMetadata.model_validate({"number": "X", "actors": ["A", "B"]})
    assert meta.actors == [
        FilmActor(name="A", gender=ActorGender.UNKNOWN),
        FilmActor(name="B", gender=ActorGender.UNKNOWN),
    ]


def test_film_actors_default_female() -> None:
    assert film_actors(["A", "", "B"]) == [
        FilmActor(name="A", gender=ActorGender.FEMALE),
        FilmActor(name="B", gender=ActorGender.FEMALE),
    ]


def test_explicit_film_actor_kept() -> None:
    meta = MediaMetadata.model_validate(
        {
            "number": "X",
            "actors": [FilmActor(name="M", gender=ActorGender.MALE), {"name": "F", "gender": "female"}],
        }
    )
    assert meta.actors[0].gender is ActorGender.MALE
    assert meta.actors[1].gender is ActorGender.FEMALE


def test_reject_invalid_gender() -> None:
    with pytest.raises(ValidationError):
        MediaMetadata.model_validate({"number": "X", "actors": [{"name": "A", "gender": "other"}]})
