"""失败原因分类与 parse_detail 的展示契约."""

import pytest
from pydantic import BaseModel, ValidationError

from amane.net.errors import FailureKind, FailureReason, RequestFailure, classify_request_error, parse_detail


class _Leaf(BaseModel):
    value: int = 0


class _Nested(BaseModel):
    leaf: _Leaf | None = None
    items: list[_Leaf] = []


class _Root(BaseModel):
    data: _Nested | None = None


def _error(payload: object) -> ValidationError:
    try:
        _Root.model_validate(payload)
    except ValidationError as exc:
        return exc
    raise AssertionError(f"expected ValidationError for {payload!r}")


class TestParseDetail:
    """detail 只承载定位信息, 不把 pydantic 全文与内部类名送给终端用户."""

    def test_single_field_path(self):
        assert parse_detail(_error({"data": {"leaf": {"value": "x"}}})) == "data.leaf.value"

    def test_list_index_in_path(self):
        assert parse_detail(_error({"data": {"items": [{"value": 1}, {"value": "x"}]}})) == "data.items.1.value"

    def test_multiple_paths_are_deduplicated(self):
        detail = parse_detail(_error({"data": {"items": [{"value": "x"}, {"value": "y"}]}}))
        assert detail == "data.items.0.value, data.items.1.value"

    def test_root_type_error_does_not_leak_class_names(self):
        detail = parse_detail(_error("not-an-object"))
        assert "_Root" not in detail
        assert detail  # 回退到可展示的定位串, 不是空串


class TestClassifyRequestError:
    def test_none_failure_is_network(self):
        assert classify_request_error(None).value == "network"

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (RequestFailure(kind=FailureKind.TIMEOUT, message="timeout"), FailureReason.TIMEOUT),
            (RequestFailure(kind=FailureKind.CURL, message="curl error"), FailureReason.NETWORK),
            (RequestFailure(kind=FailureKind.UNEXPECTED, message="unexpected"), FailureReason.UNEXPECTED),
            # HTTP_STATUS 且正文无拦截信号时按状态码分类 (正文优先于状态的规则见 test_base.py).
            (RequestFailure(kind=FailureKind.HTTP_STATUS, status=404, message="HTTP 404"), FailureReason.NOT_FOUND),
        ],
    )
    def test_kind_and_status_mapping(self, failure: RequestFailure, expected: FailureReason):
        assert classify_request_error(failure) == expected
