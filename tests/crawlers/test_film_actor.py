"""FilmActor 收旧 list[str]; 非法 gender 拒绝."""

import pytest
from pydantic import ValidationError

from amane.crawlers.models import FilmActor, MediaMetadata
from amane.enums import ActorGender


def test_coerce_string_list_to_unknown_gender() -> None:
    meta = MediaMetadata.model_validate({"number": "X", "actors": ["A", "B"]})
    assert meta.actors == [
        FilmActor(name="A", gender=ActorGender.UNKNOWN),
        FilmActor(name="B", gender=ActorGender.UNKNOWN),
    ]


def test_reject_invalid_gender() -> None:
    with pytest.raises(ValidationError):
        MediaMetadata.model_validate({"number": "X", "actors": [{"name": "A", "gender": "other"}]})
