from typing import NamedTuple

import pytest
from pydantic import BaseModel, create_model

from amane.api.models.libraries import LibraryResponse
from amane.db.models import Library
from amane.utils.model import assert_model_subset, subset_of, to_resp


def test_to_resp_null_json_list_uses_response_default() -> None:
    """JSON 列读出 None 时, 非 Optional 且有默认的响应字段用默认值 (空列表)."""
    lib = Library(id=1, name="t", path="/m")
    object.__setattr__(lib, "patterns", None)
    resp = to_resp(LibraryResponse, lib)
    assert resp.patterns == []


class _VarianceCase(NamedTuple):
    child: type
    parent: type
    covariant: bool
    contravariant: bool
    id: str


_VARIANCE_CASES = [
    _VarianceCase(create_model("Eq", x=(str, ...)), create_model("EqP", x=(str, ...)), True, True, "equal"),
    _VarianceCase(
        create_model("Narrow", x=(str, ...)),
        create_model("Wide", x=(str | None, ...)),
        True,
        False,
        "str_lt_str_or_none",
    ),
    _VarianceCase(
        create_model("WideC", x=(str | None, ...)),
        create_model("NarrowP", x=(str, ...)),
        False,
        True,
        "str_or_none_gt_str",
    ),
    _VarianceCase(
        create_model("BothOpt", x=(str | None, ...)),
        create_model("BothOptP", x=(str | None, ...)),
        True,
        True,
        "both_optional",
    ),
    _VarianceCase(
        create_model("IntX", x=(int, ...)),
        create_model("StrOrNone", x=(str | None, ...)),
        False,
        False,
        "int_vs_str",
    ),
]


@pytest.mark.parametrize("case", _VARIANCE_CASES, ids=lambda c: c.id)
def test_field_subset_variance(case: _VarianceCase) -> None:
    if case.covariant:
        assert_model_subset(case.child, case.parent, covariant=True)
    else:
        with pytest.raises(ValueError, match="covariant"):
            assert_model_subset(case.child, case.parent, covariant=True)
    if case.contravariant:
        assert_model_subset(case.child, case.parent, covariant=False)
    else:
        with pytest.raises(ValueError, match="contravariant"):
            assert_model_subset(case.child, case.parent, covariant=False)


def test_subset_rejects_unknown_field() -> None:
    class Parent(BaseModel):
        name: str

    class Child(BaseModel):
        name: str
        extra: int

    with pytest.raises(ValueError, match="fields not on Parent"):
        assert_model_subset(Child, Parent, covariant=True)
    with pytest.raises(ValueError, match="fields not on Parent"):
        assert_model_subset(Child, Parent, covariant=False)


def test_covariant_decorator_rejects_wider_field() -> None:
    class Parent(BaseModel):
        x: str

    with pytest.raises(ValueError, match="covariant"):

        @subset_of(Parent, covariant=True)
        class Child(BaseModel):
            x: str | None


def test_contravariant_decorator_rejects_narrower_field() -> None:
    class Parent(BaseModel):
        x: str | None

    with pytest.raises(ValueError, match="contravariant"):

        @subset_of(Parent, covariant=False)
        class Child(BaseModel):
            x: str
