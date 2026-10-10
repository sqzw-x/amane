"""调用方注入的解析规则: 剔除串、前缀类型、检索别名."""

from typing import NamedTuple

import pytest
from pydantic import ValidationError

from amane.config import ParsingConfig, build_number_rules
from amane.parsing import (
    EMPTY_NUMBER_RULES,
    ContentType,
    Mosaic,
    NumberRules,
    infer_content_type,
    parse_file_info,
    search_aliases_for,
)

_EMPTY = build_number_rules(ParsingConfig())


class _EscapeCase(NamedTuple):
    path: str
    escape: list[str]
    number: str
    content_type: ContentType


# #213 报告的样本: 站点署名把番号吃成 HHD-800.
ESCAPE_CASES = [
    _EscapeCase("/lib/HHD800.COM@FC2-XXXXXXX.mp4", ["HHD800.COM@"], "FC2-XXXXXXX", ContentType.FC2),
    _EscapeCase("/lib/HHD800.COM@FC2-1234567.mp4", ["HHD800.COM@"], "FC2-1234567", ContentType.FC2),
    _EscapeCase("/lib/里番/HHD800.COM@FC2-XXXXXXX.mp4", ["HHD800.COM@"], "FC2-XXXXXXX", ContentType.HENTAI),
    _EscapeCase("/lib/MIDV-123.mp4", ["HHD800.COM@"], "MIDV-123", ContentType.CENSORED),
]


@pytest.mark.parametrize(("path", "escape", "number", "content_type"), ESCAPE_CASES)
def test_escape_strings_from_rules(path: str, escape: list[str], number: str, content_type: ContentType) -> None:
    """规则里的剔除串与显式参数同一条路径, 且对目录段同样生效."""
    rules = build_number_rules(ParsingConfig(escape_strings=escape))
    info = parse_file_info(path, rules=rules)
    assert info.number == number
    assert info.content_type is content_type


def test_escape_strings_argument_overrides_rules() -> None:
    """显式参数优先于规则对象, 既有调用形态不变."""
    rules = build_number_rules(ParsingConfig(escape_strings=["HHD800.COM@"]))
    path = "/lib/HHD800.COM@FC2-XXXXXXX.mp4"
    # 不传参数时用规则里的剔除串 (基线本会把番号吃成 HHD-800).
    assert parse_file_info(path, rules=rules).number == "FC2-XXXXXXX"
    # 显式传入 (含空列表) 完整替代规则里的剔除串.
    assert parse_file_info(path, escape_strings=[], rules=rules).number == "HHD-800"
    assert parse_file_info(path, escape_strings=["FC2"], rules=rules).number == "HHD-800"


def test_escape_strings_empty_items_ignored() -> None:
    rules = build_number_rules(ParsingConfig(escape_strings=["", "   ", "FC2"]))
    assert rules.escape_strings == ("FC2",)


PREFIX_TYPE_CASES: list[tuple[str, str, ContentType]] = [
    # 用户按文件名写短形态, 也要命中带枚举前缀的番号.
    ("300MIUM-123.mp4", "MIUM", ContentType.UNCENSORED),
    # 键带尾部分隔符与不带是同一件.
    ("HEYZO-1234.mp4", "HEYZO-", ContentType.CENSORED),
    ("HEYZO-1234.mp4", "heyzo", ContentType.CENSORED),
]


@pytest.mark.parametrize(("path", "key", "expected"), PREFIX_TYPE_CASES)
def test_prefix_types_key_forms(path: str, key: str, expected: ContentType) -> None:
    rules = build_number_rules(ParsingConfig(prefix_types={key: expected}))
    assert parse_file_info(path, rules=rules).content_type is expected


def test_prefix_types_long_form_wins_over_short() -> None:
    """同一前缀两种写法都给了时, 长形态优先 (用户写什么就是什么, 短形态只补齐)."""
    rules = build_number_rules(
        ParsingConfig(prefix_types={"SIRO-": ContentType.WESTERN, "SIRO": ContentType.UNCENSORED})
    )
    assert parse_file_info("SIRO-1234.mp4", rules=rules).content_type is ContentType.UNCENSORED


def test_prefix_types_covers_builtin_fallback() -> None:
    """覆盖内置的无码前缀兜底."""
    rules = build_number_rules(ParsingConfig(prefix_types={"HEYZO": ContentType.CENSORED}))
    info = parse_file_info("HEYZO-1234.mp4", rules=rules)
    assert info.content_type is ContentType.CENSORED
    assert info.mosaic is Mosaic.CENSORED


def test_prefix_types_loses_to_directory_keyword() -> None:
    """目录关键词是更具体的声明, 因此压过前缀约定."""
    western = build_number_rules(ParsingConfig(prefix_types={"MIUM": ContentType.AMATEUR}))
    assert parse_file_info("/lib/欧美/MIUM-123.mp4", rules=western).content_type is ContentType.WESTERN
    assert parse_file_info("/lib/里番/MIUM-123.mp4", rules=western).content_type is ContentType.HENTAI


def test_prefix_types_does_not_rewrite_prefix() -> None:
    """短形态命中只放宽类型判定, 前缀本身不变 (路径模板 `{prefix?}` 读的是它)."""
    rules = build_number_rules(ParsingConfig(prefix_types={"MIUM": ContentType.CENSORED}))
    assert parse_file_info("300MIUM-123.mp4", rules=rules).prefix == "300MIUM"


def test_prefix_types_short_form_not_registered_outside_builtin_table() -> None:
    """`H4610` 的折叠形态 `H` 不在内置表里, 不注册, 因此不会命中无关番号."""
    rules = build_number_rules(ParsingConfig(prefix_types={"H": ContentType.UNCENSORED}))
    assert parse_file_info("H4610-123.mp4", rules=rules).content_type is not ContentType.UNCENSORED


def test_prefix_types_rejects_empty_prefix() -> None:
    with pytest.raises(ValueError, match="不得为空"):
        ParsingConfig(prefix_types={"  ": ContentType.AMATEUR})


def test_prefix_types_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        ParsingConfig.model_validate({"prefix_types": {"MIDV": "not-a-type"}})


def test_prefix_types_normalization_is_observable() -> None:
    """三种键写法对同一批文件给出一致结论 —— 归一化与短形态的可观察契约."""
    variants = [
        ParsingConfig(prefix_types={"mium": ContentType.AMATEUR}),
        ParsingConfig(prefix_types={"MIUM ": ContentType.AMATEUR}),
        ParsingConfig(prefix_types={"300Mium": ContentType.AMATEUR}),
    ]
    numbers = ["300MIUM-123.mp4", "MIUM-123.mp4", "MIDV-123.mp4"]
    results = [{infer_content_type(n, rules=build_number_rules(v)) for n in numbers} for v in variants]
    assert results[0] == results[1] == results[2]


def test_default_config_matches_empty_rules() -> None:
    """缺省配置不改解析行为."""
    paths = ["MIDV-123-UC-4K.mp4", "/lib/里番/MD0165-1.mp4", "300MIUM-123.mp4", "HHD800.COM@FC2-XXXXXXX.mp4"]
    for path in paths:
        assert parse_file_info(path, rules=_EMPTY) == parse_file_info(path, rules=EMPTY_NUMBER_RULES)


def test_number_rules_is_json_serializable() -> None:
    rules = build_number_rules(ParsingConfig(escape_strings=["X"], prefix_types={"MIDV": ContentType.CENSORED}))
    assert rules.model_dump(mode="json")["prefix_types"] == {"MIDV": "censored"}


def test_empty_rules_reject_mutation() -> None:
    """共享常量不可写: 冻结模型拒绝赋值, 因此缺省规则不会被某个调用方改坏."""
    with pytest.raises(ValidationError):
        EMPTY_NUMBER_RULES.escape_strings = ("x",)  # type: ignore[misc]


ALIAS_CASES: list[tuple[str, tuple[str, ...]]] = [
    ("300MIUM-123", ("MIUM",)),
    ("MIUM-123", ("300MIUM",)),
    ("MIUM00123", ("300MIUM",)),
    ("259LUXU-99", ("LUXU",)),
    ("LUXU-99", ("259LUXU",)),
    ("SIRO-1234", ()),
    ("MIDV-123", ()),
]


@pytest.mark.parametrize(("number", "expected"), ALIAS_CASES)
def test_auto_search_aliases_both_directions(number: str, expected: tuple[str, ...]) -> None:
    """自动别名对内置别名表的键与值双向补齐, 与本地番号落在哪一侧无关."""
    assert search_aliases_for(number, _EMPTY) == expected


def test_auto_search_aliases_disabled() -> None:
    rules = build_number_rules(ParsingConfig(auto_search_aliases=False))
    assert search_aliases_for("300MIUM-123", rules) == ()


def test_manual_search_aliases_override_auto() -> None:
    rules = build_number_rules(ParsingConfig(search_aliases={"300MIUM": ["MIUM-999"]}))
    assert search_aliases_for("300MIUM-123", rules) == ("MIUM-999",)
    # 未覆盖的另一侧仍走自动.
    assert search_aliases_for("MIUM-123", rules) == ("300MIUM",)


def test_search_aliases_reject_empty_key() -> None:
    with pytest.raises(ValueError, match="不得为空"):
        ParsingConfig(search_aliases={" ": ["X"]})


def test_search_aliases_drop_empty_values() -> None:
    rules = build_number_rules(ParsingConfig(search_aliases={"MIDV": ["", "  ", "X"]}))
    assert rules.search_aliases["MIDV"] == ("X",)


def test_number_rules_direct_construction_bypasses_normalization() -> None:
    """直接构造视为已归一 (唯一归一入口是 build_number_rules), 行为与手写键一致."""
    direct = NumberRules(prefix_types={"MIDV": ContentType.UNCENSORED})
    assert parse_file_info("MIDV-123.mp4", rules=direct).content_type is ContentType.UNCENSORED
