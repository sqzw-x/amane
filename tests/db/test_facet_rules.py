"""分类规则纯函数表测试 (单跳别名 / 剔除)."""

from __future__ import annotations

import pytest

from amane.db.facet_rules import RuleEntry, apply_list, apply_scalar, resolve_name
from amane.db.models import FacetRuleAction

ALIAS_A_B = {"A": RuleEntry(FacetRuleAction.ALIAS, "B")}
ALIAS_CHAIN_COMPRESSED = {
    "A": RuleEntry(FacetRuleAction.ALIAS, "C"),
    "B": RuleEntry(FacetRuleAction.ALIAS, "C"),
}
BLOCK_B = {"B": RuleEntry(FacetRuleAction.BLOCK)}
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
