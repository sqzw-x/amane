import assert from "node:assert/strict";
import { test } from "node:test";
import { defaultParseSearch, defaultStringifySearch } from "@tanstack/react-router";
import {
  FilterExpressionError,
  filterRuleError,
  parseFilterFormula,
  serializeFilterFormula,
  type FilterEntity,
  formulaFromTree,
  type FilterGroup,
  type FilterRule,
} from "@/lib/filter-expression";

type LegacyExpression = { version: 1; expression: FilterGroup };

function envelope(rule: FilterRule): LegacyExpression {
  return { version: 1, expression: { type: "group", op: "and", children: [rule] } };
}

function nested(depth: number): LegacyExpression {
  let expression: FilterGroup = {
    type: "group",
    op: "and",
    children: [{ type: "rule", field: "has_image", operator: "eq", value: false }],
  };
  for (let index = 1; index < depth; index += 1)
    expression = { type: "group", op: "or", negate: index % 2 === 1, children: [expression] };
  return { version: 1, expression };
}

function hasError(code: string): (error: unknown) => boolean {
  return (error) =>
    error instanceof FilterExpressionError &&
    error.code === code &&
    error.path.length > 0 &&
    error.messageKey.startsWith("advancedFilter.errors.");
}

test("嵌套表达式保留实体、逻辑、数值与布尔值并支持分享往返", () => {
  const cases: { entity: FilterEntity; value: LegacyExpression }[] = [
    {
      entity: "metadata",
      value: {
        version: 1,
        expression: {
          type: "group",
          op: "and",
          children: [
            { type: "rule", field: "actor_id", operator: "eq", value: 42 },
            {
              type: "group",
              op: "or",
              negate: true,
              children: [
                { type: "rule", field: "title", operator: "contains", value: '海辺 " + & ? 🌊' },
                { type: "rule", field: "has_subtitle", operator: "eq", value: false },
              ],
            },
          ],
        },
      },
    },
    {
      entity: "actor",
      value: {
        version: 1,
        expression: {
          type: "group",
          op: "or",
          children: [
            { type: "rule", field: "gender", operator: "eq", value: "female" },
            {
              type: "group",
              op: "and",
              negate: true,
              children: [
                { type: "rule", field: "height", operator: "gte", value: 170 },
                { type: "rule", field: "birthday", operator: "lte", value: "2000-02-29" },
              ],
            },
          ],
        },
      },
    },
  ];
  for (const { entity, value } of cases) {
    const formula = formulaFromTree(value.expression);
    const encoded = serializeFilterFormula(formula, entity);
    assert.equal(typeof encoded, "string");
    const search = defaultStringifySearch({ f: encoded });
    assert.ok(search.slice(3).length <= 4096);
    const parsed: unknown = defaultParseSearch(search);
    assert.ok(typeof parsed === "object" && parsed !== null && "f" in parsed);
    assert.deepEqual(parseFilterFormula(parsed.f, entity), formula);
    assert.deepEqual(parseFilterFormula(encoded, entity), formula);
    assert.deepEqual(parseFilterFormula(value, entity), formula);
  }
  assert.equal(parseFilterFormula(undefined, "actor"), null);
  assert.equal(parseFilterFormula({ version: 1, expression: null }, "metadata"), null);
  assert.equal(serializeFilterFormula(null, "metadata"), undefined);
  const cup = parseFilterFormula(
    envelope({ type: "rule", field: "cup", operator: "gte", value: "aa" }),
    "actor",
  );
  assert.equal(cup?.terms[0]?.node.type === "rule" && cup.terms[0].node.value, "AA");
});

test("损坏链接、未知版本与非法实体字段保留明确错误", () => {
  const cases: { input: unknown; entity: FilterEntity; code: string }[] = [
    { input: "", entity: "metadata", code: "json" },
    { input: "{", entity: "metadata", code: "json" },
    { input: "null", entity: "metadata", code: "shape" },
    { input: [], entity: "metadata", code: "shape" },
    {
      input: {
        version: 1,
        expression: {
          type: "group",
          op: "and",
          children: [{ type: "rule", field: "unsupported_field", operator: "eq", value: true }],
        },
      },
      entity: "metadata",
      code: "field",
    },
    { input: { version: 3, expression: null }, entity: "metadata", code: "version" },
    {
      input: { version: 2, formula: { type: "group", terms: [] } },
      entity: "metadata",
      code: "shape",
    },
    {
      input: {
        version: 2,
        formula: {
          type: "group",
          terms: [{ join: "or", node: { type: "rule", field: "vr", operator: "eq", value: true } }],
        },
      },
      entity: "metadata",
      code: "shape",
    },
    {
      input: {
        version: 1,
        expression: {
          type: "group",
          op: "and",
          implicit: true,
          children: [
            { type: "rule", field: "vr", operator: "eq", value: true },
            { type: "rule", field: "has_files", operator: "eq", value: true },
          ],
        },
      },
      entity: "metadata",
      code: "shape",
    },
    {
      input: { version: 1, expression: { type: "group", op: "and", children: [] } },
      entity: "metadata",
      code: "shape",
    },
    {
      input: { version: 1, expression: { type: "group", op: "and", negate: "true", children: [] } },
      entity: "metadata",
      code: "shape",
    },
    {
      input: {
        ...envelope({ type: "rule", field: "title", operator: "contains", value: "a" }),
        saved_query_id: 1,
      },
      entity: "metadata",
      code: "shape",
    },
    {
      input: envelope({ type: "rule", field: "title", operator: "contains", value: "a" }),
      entity: "actor",
      code: "field",
    },
    {
      input: envelope({ type: "rule", field: "title", operator: "eq", value: "a" }),
      entity: "metadata",
      code: "operator",
    },
    {
      input: envelope({ type: "rule", field: "actor_id", operator: "eq", value: "42" }),
      entity: "metadata",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "actor_id", operator: "eq", value: -1 }),
      entity: "metadata",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "has_files", operator: "eq", value: "false" }),
      entity: "metadata",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "number", operator: "contains", value: "  " }),
      entity: "metadata",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "content_type", operator: "eq", value: "unknown" }),
      entity: "metadata",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "height", operator: "gte", value: 1.5 }),
      entity: "actor",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "height", operator: "gte", value: Infinity }),
      entity: "actor",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "birthday", operator: "lte", value: "2001-02-29" }),
      entity: "actor",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "birthday", operator: "lte", value: "0000-01-01" }),
      entity: "actor",
      code: "value",
    },
    {
      input: envelope({ type: "rule", field: "cup", operator: "gte", value: "A1" }),
      entity: "actor",
      code: "value",
    },
  ];
  for (const { input, entity, code } of cases)
    assert.throws(() => parseFilterFormula(input, entity), hasError(code));
});

test("深度、节点、Unicode 文本与分享地址边界分别校验", () => {
  assert.ok(parseFilterFormula(nested(8), "actor"));
  assert.throws(() => parseFilterFormula(nested(9), "actor"), hasError("depth"));
  const rule: FilterRule = {
    type: "rule",
    field: "title",
    operator: "contains",
    value: "🌊".repeat(200),
  };
  assert.equal(filterRuleError("metadata", rule), null);
  assert.equal(
    filterRuleError("metadata", { ...rule, value: "🌊".repeat(201) }),
    "advancedFilter.errors.textLength",
  );
  const many: LegacyExpression = {
    version: 1,
    expression: {
      type: "group",
      op: "and",
      children: Array.from({ length: 256 }, () => ({
        type: "rule",
        field: "has_files",
        operator: "eq",
        value: false,
      })),
    },
  };
  assert.throws(
    () => serializeFilterFormula(formulaFromTree(many.expression), "metadata"),
    hasError("nodes"),
  );
  const ids: LegacyExpression = {
    version: 1,
    expression: {
      type: "group",
      op: "or",
      children: Array.from({ length: 200 }, () => ({
        type: "rule",
        field: "actor_id",
        operator: "eq",
        value: 42,
      })),
    },
  };
  assert.throws(
    () => serializeFilterFormula(formulaFromTree(ids.expression), "metadata"),
    hasError("length"),
  );
  ids.expression.children.push({ type: "rule", field: "library_id", operator: "eq", value: 7 });
  assert.throws(
    () => serializeFilterFormula(formulaFromTree(ids.expression), "metadata"),
    hasError("ids"),
  );
  const long: LegacyExpression = {
    version: 1,
    expression: { type: "group", op: "or", children: [rule, { ...rule, value: "海".repeat(200) }] },
  };
  assert.throws(
    () => serializeFilterFormula(formulaFromTree(long.expression), "metadata"),
    hasError("length"),
  );
  assert.throws(() => parseFilterFormula(JSON.stringify(long), "metadata"), hasError("length"));
});
