"""参数签名渲染: 目录索引的紧凑表示.

签名是原始 JSON Schema 的有损替代, 校验仍在服务端. 这里锁定「哪些信息必须留下」:
类型、必填性、枚举、数值范围、嵌套结构. 丢掉的是 Schema 的书写形式.
"""

from typing import Any

import pytest

from amane.agent.signature import signature, summary


@pytest.mark.parametrize(
    ("params", "want"),
    [
        pytest.param(None, "", id="无 Schema"),
        pytest.param({}, "", id="空 Schema"),
        pytest.param({"type": "object", "properties": {}}, "", id="无参"),
        pytest.param(
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
            "id: int",
            id="单个必填",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "note": {"type": "string"}},
                "required": ["id"],
            },
            "id: int, note?: string",
            id="可选字段带问号",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["ids"],
            },
            "ids: [int]",
            id="数组",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {"mode": {"enum": ["a", "b"]}},
                "required": ["mode"],
            },
            "mode: a|b",
            id="内联枚举",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {"n": {"type": "integer", "minimum": 60, "maximum": 86400}},
                "required": ["n"],
            },
            "n: int(60~86400)",
            id="数值范围",
        ),
    ],
)
def test_signature(params: dict[str, Any] | None, want: str) -> None:
    assert signature(params) == want


def test_signature_flattens_ref_enum() -> None:
    """$ref 指向枚举时展开取值, 让调用方不必再取一次详情."""
    params = {
        "type": "object",
        "properties": {"content_type": {"$ref": "#/$defs/ContentType"}},
        "required": ["content_type"],
        "$defs": {"ContentType": {"enum": ["censored", "western"]}},
    }
    assert signature(params) == "content_type: censored|western"


def test_signature_expands_nested_object() -> None:
    params = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
                "required": ["url"],
            }
        },
        "required": ["patch"],
    }
    assert signature(params) == "patch: {name?, url}"


def test_signature_nullable_field_does_not_double_mark() -> None:
    """可选字段本身已带 `?`, 类型里的 null 不再重复标记."""
    params = {
        "type": "object",
        "properties": {"name": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": [],
    }
    assert signature(params) == "name?: string"


def test_signature_keeps_nullable_marker_when_required() -> None:
    params = {
        "type": "object",
        "properties": {"name": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": ["name"],
    }
    assert signature(params) == "name: string?"


def test_signature_collapses_long_enum() -> None:
    """枚举过长时只给类型名, 免得一行签名被撑开."""
    params = {
        "type": "object",
        "properties": {"tag": {"enum": [f"t{i}" for i in range(20)]}},
        "required": ["tag"],
    }
    assert signature(params) == "tag: enum"


def test_signature_stops_at_depth_limit() -> None:
    """过深的嵌套只给 object, 避免签名比原 Schema 还长."""
    deep: dict[str, Any] = {"type": "object", "properties": {"leaf": {"type": "string"}}}
    for _ in range(10):
        deep = {"type": "object", "properties": {"inner": deep}}
    params = {"type": "object", "properties": {"root": deep}, "required": ["root"]}
    rendered = signature(params)
    assert rendered.count("{") <= 7, f"嵌套未收敛: {rendered}"


@pytest.mark.parametrize(
    ("description", "want"),
    [
        pytest.param("单行", "单行", id="单行"),
        pytest.param("首行\n\n次行", "首行", id="取首行"),
        pytest.param("\n\n  首行  \n次行", "首行", id="跳过空行并去空白"),
        pytest.param("", "", id="空描述"),
        pytest.param("   \n\t\n", "", id="全空白"),
    ],
)
def test_summary(description: str, want: str) -> None:
    assert summary(description) == want
