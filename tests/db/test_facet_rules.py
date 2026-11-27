"""分类规则纯函数表测试 (单跳别名 / 剔除)."""

from __future__ import annotations

import pytest

from amane.crawlers.models import FilmActor, MediaMetadata
from amane.db.facet_rules import (
    FacetFieldValues,
    ResolvedField,
    RuleEntry,
    RuleRow,
    RuleSet,
    apply_list,
    apply_scalar,
    resolve_field_value,
    resolve_name,
    rule_lookup_names,
    rules_from_rows,
)
from amane.db.models import FacetKind, FacetRuleAction
from amane.enums import MetadataField

ALIAS_A_B = {"A": RuleEntry(FacetRuleAction.ALIAS, "B")}
ALIAS_CHAIN_COMPRESSED = {
    "A": RuleEntry(FacetRuleAction.ALIAS, "C"),
    "B": RuleEntry(FacetRuleAction.ALIAS, "C"),
}
BLOCK_B = {"B": RuleEntry(FacetRuleAction.BLOCK)}
BLOCK_A: dict[str, RuleEntry] = {"A": RuleEntry(FacetRuleAction.BLOCK)}
ALIAS_TO_BLOCKED = {
    "A": RuleEntry(FacetRuleAction.ALIAS, "B"),
    "B": RuleEntry(FacetRuleAction.BLOCK),
}


@pytest.mark.parametrize(
    ("name", "rules", "expected"),
    [
        ("X", {}, "X"),
        ("A", ALIAS_A_B, "B"),
        ("A", ALIAS_CHAIN_COMPRESSED, "C"),
        ("B", BLOCK_B, None),
        ("A", ALIAS_TO_BLOCKED, None),
        ("B", ALIAS_TO_BLOCKED, None),
        ("", {}, ""),
    ],
    ids=[
        "passthrough",
        "single_alias",
        "compressed_alias",
        "block",
        "alias_target_blocked",
        "blocked_source",
        "empty_string",
    ],
)
def test_resolve_name(name: str, rules: dict[str, RuleEntry], expected: str | None) -> None:
    assert resolve_name(name, rules) == expected


@pytest.mark.parametrize(
    ("names", "rules", "expected"),
    [
        (["A", "Carol"], ALIAS_A_B, ["B", "Carol"]),
        (["A", "B", "Carol"], ALIAS_CHAIN_COMPRESSED, ["C", "Carol"]),
        (["A", "B", "Carol"], ALIAS_TO_BLOCKED, ["Carol"]),
        (["A", "A", ""], ALIAS_A_B, ["B"]),
        ([], ALIAS_A_B, []),
    ],
    ids=["alias_one", "compressed_dedupe", "drop_blocked", "dedupe_skip_empty", "empty"],
)
def test_apply_list(names: list[str], rules: dict[str, RuleEntry], expected: list[str]) -> None:
    assert apply_list(names, rules) == expected


@pytest.mark.parametrize(
    ("value", "rules", "expected"),
    [
        ("A", ALIAS_A_B, "B"),
        ("B", BLOCK_B, None),
        (None, ALIAS_A_B, None),
        ("", ALIAS_A_B, None),
        ("StudioX", {}, "StudioX"),
    ],
    ids=["alias", "block", "none", "empty", "passthrough"],
)
def test_apply_scalar(value: str | None, rules: dict[str, RuleEntry], expected: str | None) -> None:
    assert apply_scalar(value, rules) == expected


class TestRuleLookupNames:
    """候选名收集: 演员带别名拆分名, 标量只取非空值."""

    @pytest.mark.parametrize(
        ("desc", "values", "expected"),
        [
            (
                "演员同时给出原始名与展示名",
                FacetFieldValues(actors=["Mei(みい)"], directors=[], tags=[]),
                {FacetKind.ACTOR: {"Mei(みい)", "Mei"}},
            ),
            (
                "列表去重后按种类分组",
                FacetFieldValues(actors=[], directors=["D1"], tags=["T1", "T1"]),
                {FacetKind.DIRECTOR: {"D1"}, FacetKind.TAG: {"T1"}},
            ),
            (
                "标量只收非空值",
                FacetFieldValues(actors=[], directors=[], tags=[], studio="S", series=""),
                {FacetKind.STUDIO: {"S"}},
            ),
            ("全空 → 无可查名字", FacetFieldValues(actors=[], directors=[], tags=[]), {}),
        ],
        ids=["actor_alias_split", "grouped_dedupe", "scalar_skips_empty", "all_empty"],
    )
    def test_names(self, desc: str, values: FacetFieldValues, expected: dict[FacetKind, set[str]]) -> None:
        assert rule_lookup_names(values) == expected, desc


class TestRulesFromRows:
    """定向查询结果转换: 目标被 block 的别名收敛为 block, 单跳仍在一次查询内判完."""

    @pytest.mark.parametrize(
        ("desc", "rows", "expected"),
        [
            ("block 行照写", [(FacetKind.SERIES, "A", FacetRuleAction.BLOCK, None, None)], BLOCK_A),
            (
                "alias 目标未被 block → 保留别名",
                [(FacetKind.SERIES, "A", FacetRuleAction.ALIAS, "B", None)],
                {"A": RuleEntry(FacetRuleAction.ALIAS, "B")},
            ),
            (
                "alias 目标被 block → 收敛为 block",
                [(FacetKind.SERIES, "A", FacetRuleAction.ALIAS, "B", FacetRuleAction.BLOCK)],
                BLOCK_A,
            ),
            ("未知分类跳过", [(FacetKind.USER_TAG, "A", FacetRuleAction.BLOCK, None, None)], {}),
        ],
        ids=["block", "alias", "alias_to_blocked", "skip_user_tag"],
    )
    def test_rows(self, desc: str, rows: list[RuleRow], expected: dict[str, RuleEntry]) -> None:
        by_kind = rules_from_rows(rows)
        series = by_kind.get(FacetKind.SERIES, {})
        assert series == expected, desc


class TestResolveFieldValue:
    """单个来源的取值折叠: 多值整份剔除才为空, 部分保留照常采用."""

    @pytest.mark.parametrize(
        ("desc", "field", "data", "rules", "expected"),
        [
            (
                "系列被剔除 → 空值",
                MetadataField.SERIES,
                MediaMetadata(number="X", series="B"),
                {FacetKind.SERIES: BLOCK_B},
                ResolvedField(value=None),
            ),
            (
                "系列别名 → 改写值",
                MetadataField.SERIES,
                MediaMetadata(number="X", series="A"),
                {FacetKind.SERIES: ALIAS_A_B},
                ResolvedField(value="B"),
            ),
            (
                "标签部分剔除 → 保留剩余",
                MetadataField.TAGS,
                MediaMetadata(number="X", tags=["A", "Carol"]),
                {FacetKind.TAG: BLOCK_B},
                ResolvedField(names=["A", "Carol"]),
            ),
            (
                "标签整份剔除 → 空列表",
                MetadataField.TAGS,
                MediaMetadata(number="X", tags=["B"]),
                {FacetKind.TAG: BLOCK_B},
                ResolvedField(names=[]),
            ),
            (
                "演员整份剔除 → 空列表",
                MetadataField.ACTORS,
                MediaMetadata(number="X", actors=[FilmActor(name="B")]),
                {FacetKind.ACTOR: BLOCK_B},
                ResolvedField(actors=[]),
            ),
            (
                "非分类字段原样返回",
                MetadataField.TITLE,
                MediaMetadata(number="X", title="T"),
                {},
                ResolvedField(value="T"),
            ),
        ],
        ids=["series_blocked", "series_alias", "tags_partial", "tags_all", "actors_all", "title_passthrough"],
    )
    def test_resolve(
        self,
        desc: str,
        field: MetadataField,
        data: MediaMetadata,
        rules: dict[FacetKind, dict[str, RuleEntry]],
        expected: ResolvedField,
    ) -> None:
        assert resolve_field_value(field, data, RuleSet(rules=rules)) == expected, desc

    @pytest.mark.parametrize(
        ("desc", "resolved", "expected"),
        [
            ("空标量", ResolvedField(value=None), True),
            ("空名单", ResolvedField(names=[]), True),
            ("空出演者", ResolvedField(actors=[]), True),
            ("有标量", ResolvedField(value="X"), False),
            ("有名单", ResolvedField(names=["X"]), False),
        ],
        ids=["empty_scalar", "empty_names", "empty_actors", "filled_scalar", "filled_names"],
    )
    def test_empty_flag(self, desc: str, resolved: ResolvedField, expected: bool) -> None:
        assert resolved.empty is expected, desc
