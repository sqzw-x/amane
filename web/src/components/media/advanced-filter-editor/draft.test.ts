import assert from "node:assert/strict";
import { test } from "node:test";
import {
  compileFilterFormula,
  formulaFromTree,
  parseFilterFormula,
  type FilterGroup,
  type FilterNode,
  type FilterRule,
} from "@/lib/filter-expression";
import {
  appendNodes,
  canWrap,
  emptyDraft,
  moveOperand,
  moveSibling,
  operandsOf,
  removeSelection,
  toggleConnector,
  toggleNegate,
  ungroup,
  wrapSelection,
  type FilterDraft,
} from "./draft";

const rule = (field: FilterRule["field"]): FilterRule => ({
  type: "rule",
  field,
  operator: "eq",
  value: true,
});
const [a, b, c, d] = [rule("has_files"), rule("vr"), rule("has_subtitle"), rule("uncensored")];
const and = (...children: FilterNode[]): FilterGroup => ({ type: "group", op: "and", children });
const or = (...children: FilterNode[]): FilterGroup => ({ type: "group", op: "or", children });
const draft = formulaFromTree;
const mixedDraft = (...children: (FilterNode | (FilterGroup & { implicit: true }))[]) => {
  const formula = parseFilterFormula(
    { version: 1, expression: { type: "group", op: "or", children } },
    "metadata",
  );
  assert.ok(formula);
  return formula;
};
const range = (start: number, end = start) => ({ start, end });

function compiled(value: FilterDraft | null) {
  assert.ok(value);
  return compileFilterFormula(value, "metadata") ?? undefined;
}

test("末尾追加的与只结合最后一段, 保留手动分组与否定", () => {
  for (const [initial, expected] of [
    [or(a, b), or(a, and(b, c))],
    [and(or(a, b)), and(or(a, b), c)],
    [{ ...or(a, b), negate: true }, and({ ...or(a, b), negate: true }, c)],
  ]) {
    assert.deepEqual(compiled(appendNodes(draft(initial), [c])), expected);
  }
  const withRange = appendNodes(draft(or(a, b)), [c, d]);
  assert.deepEqual(compiled(withRange), or(a, and(b, c, d)));
  assert.equal(compileFilterFormula(emptyDraft(), "metadata"), null);
});

test("删除括号时保留组内连接符, 否定组必须先取消否定", () => {
  for (const [initial, path, expected] of [
    [and(or(a, b), c), [0], or(a, and(b, c))],
    [and(a, or(b, c), d), [1], or(and(a, b), and(c, d))],
    [and(a, and(b, c)), [1], and(a, b, c)],
  ] satisfies [FilterGroup, number[], FilterGroup][]) {
    assert.deepEqual(compiled(ungroup(draft(initial), path)), expected);
  }
  const negated = draft(and({ ...or(a, b), negate: true }, c));
  assert.equal(ungroup(negated, [0]), null);
  assert.equal(ungroup(negated, []), null);
  assert.equal(ungroup(negated, [1]), null);
  const restored = toggleNegate(negated, [0]);
  assert.ok(restored);
  assert.deepEqual(compiled(ungroup(restored, [0])), or(a, and(b, c)));
});

test("切换连接符只影响被切换的位置, 编译时与优先于或", () => {
  const mixed = mixedDraft(a, { ...and(b, c), implicit: true }, d);
  for (const [position, expected] of [
    [1, or(and(a, b, c), d)],
    [2, or(a, b, c, d)],
    [3, or(a, and(b, c, d))],
  ] satisfies [number, FilterGroup][]) {
    const toggled = toggleConnector(mixed, [], position);
    assert.deepEqual(compiled(toggled), expected);
    assert.ok(toggled);
    assert.deepEqual(toggleConnector(toggled, [], position), mixed);
  }
  assert.equal(toggleConnector(mixed, [], 0), null);
  assert.equal(toggleConnector(mixed, [], 4), null);
  const nested = draft(and(a, or(b, c)));
  assert.deepEqual(compiled(toggleConnector(nested, [1], 1)), and(a, and(b, c)));
});

test("加括号和否定遵循显式组边界", () => {
  const mixed = mixedDraft(a, { ...and(b, c), implicit: true }, d);
  assert.deepEqual(compiled(wrapSelection(mixed, range(0, 1))), or(and(or(a, b), c), d));
  assert.equal(canWrap(mixed, range(0)), false);
  assert.equal(canWrap(mixed, null), false);
  const nested = draft(and(or(a, b), c));
  assert.equal(canWrap(nested, range(1, 3)), false);
  assert.deepEqual(
    compiled(wrapSelection(nested, range(0), true)),
    and({ ...or(a, b), negate: true }, c),
  );
  assert.deepEqual(
    compiled(wrapSelection(nested, range(3), true)),
    and(or(a, b), { ...and(c), negate: true }),
  );
});

test("删除条件不留下空组, 移动条件不改连接符", () => {
  const nested = draft(and(or(a, b), c));
  assert.deepEqual(compiled(removeSelection(nested, range(1, 2))), and(c));
  assert.equal(compiled(removeSelection(nested, range(0, 3))), undefined);
  const mixed = mixedDraft(a, { ...and(b, c), implicit: true });
  assert.deepEqual(compiled(moveSibling(mixed, [0], 1)), or(b, and(a, c)));
  assert.deepEqual(compiled(moveOperand(mixed, [0], [2])), or(b, and(c, a)));
  assert.equal(moveOperand(nested, [0, 0], [1]), null);
  assert.equal(moveSibling(nested, [1], 1), null);
  assert.deepEqual(
    operandsOf(nested).map(({ path }) => path.join(".")),
    ["0", "0.0", "0.1", "1"],
  );
});
