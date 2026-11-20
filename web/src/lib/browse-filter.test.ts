import assert from "node:assert/strict";
import { test } from "node:test";
import { andLegacyFilters, readBrowseFilter } from "@/lib/browse-filter";
import {
  applyActorExpression,
  actorListQueryFromSearch,
  actorQuickFilterNodes,
  actorsBrowseSearchSchema,
  stripDefaultActorGender,
} from "@/lib/actors/browse";
import {
  parseFilterFormula,
  serializeFilterFormula,
  compileFilterFormula,
  type FilterGroup,
  type FilterRule,
} from "@/lib/filter-expression";
import { metaSearchSchema, metadataQuickFilterNodes } from "@/lib/media/browse";

const eq = (field: FilterRule["field"], value: FilterRule["value"]): FilterRule => ({
  type: "rule",
  field,
  operator: "eq",
  value,
});

const fileExpression = {
  version: 1,
  expression: {
    type: "group",
    op: "or",
    negate: true,
    children: [eq("has_files", false), eq("vr", true)],
  },
};

test("片库混合链接保留外层 AND 与旧参数各自的结合方式", () => {
  const search = metaSearchSchema.parse({ actor_id: [2, 3], library_id: [7, 8] });
  const nodes = metadataQuickFilterNodes(search);
  const oldOnly = readBrowseFilter("metadata", search, nodes);
  const legacy = [
    { type: "group", op: "and", children: [eq("actor_id", 2), eq("actor_id", 3)] },
    { type: "group", op: "or", children: [eq("library_id", 7), eq("library_id", 8)] },
  ];
  assert.equal(oldOnly.active, false);
  assert.deepEqual(compileFilterFormula(oldOnly.editorValue, "metadata")?.children, legacy);
  assert.equal(oldOnly.expression, null);

  const mixed = readBrowseFilter("metadata", { ...search, f: fileExpression }, nodes);
  assert.deepEqual(mixed.editorValue, parseFilterFormula(fileExpression, "metadata"));
  assert.deepEqual(mixed.expression, {
    type: "group",
    op: "and",
    children: [...legacy, fileExpression.expression],
  });
  const migrated = serializeFilterFormula(andLegacyFilters(null, mixed.outerFilters), "metadata");
  assert.deepEqual(readBrowseFilter("metadata", { f: migrated }, []).expression?.children, legacy);
});

test("旧条件合并到已有与组时维持表达式深度", () => {
  let expression: FilterGroup = { type: "group", op: "and", children: [eq("vr", true)] };
  for (let depth = 1; depth < 8; depth += 1) {
    expression = { type: "group", op: "and", children: [expression] };
  }
  const legacy = eq("has_files", true);
  const merged = andLegacyFilters(parseFilterFormula({ version: 1, expression }, "metadata"), [
    legacy,
  ]);
  const encoded = serializeFilterFormula(merged, "metadata");
  assert.deepEqual(readBrowseFilter("metadata", { f: encoded }, []).expression?.children, [
    legacy,
    ...expression.children,
  ]);
});

test("应用和重开保留手动括号, 查询不携带分组标记", () => {
  const a = eq("has_files", true);
  const b = eq("vr", true);
  const c = eq("has_subtitle", true);
  const conjunction: FilterGroup = { type: "group", op: "and", children: [b, c] };
  for (const implicit of [false, true]) {
    const value = {
      version: 1,
      expression: {
        type: "group",
        op: "or",
        children: [a, implicit ? { ...conjunction, implicit: true } : conjunction],
      },
    };
    const old = readBrowseFilter("metadata", { f: JSON.stringify(value) }, []);
    const formula = old.editorValue;
    assert.ok(formula);
    assert.equal(formula.terms.length, implicit ? 3 : 2);
    const f = serializeFilterFormula(formula, "metadata");
    assert.deepEqual(JSON.parse(f ?? "null"), { version: 2, formula });
    assert.ok(!f?.includes("implicit"));
    const filter = readBrowseFilter("metadata", { f }, []);
    assert.deepEqual(filter.editorValue, formula);
    const query = { type: "group", op: "or", children: [a, conjunction] };
    assert.deepEqual(filter.expression, query);
    assert.deepEqual(old.expression, query);
    const legacy = eq("actor_id", 7);
    const mixed = readBrowseFilter("metadata", { f }, [legacy]);
    assert.deepEqual(mixed.expression, { type: "group", op: "and", children: [legacy, query] });
  }
});

test("非法筛选链接和查询预设冲突阻止列表请求", () => {
  for (const [search, errorCode, conflict] of [
    [{ f: "{" }, "json", false],
    [{ f: fileExpression, saved_query_id: 3 }, undefined, true],
  ] satisfies [{ f: unknown; saved_query_id?: number }, string | undefined, boolean][]) {
    const filter = readBrowseFilter("metadata", search, []);
    assert.equal(filter.active, true);
    assert.equal(filter.blocked, true);
    assert.equal(filter.error?.code, errorCode);
    assert.equal(filter.conflict, conflict);
  }
  assert.equal(readBrowseFilter("metadata", { saved_query_id: 3 }, []).blocked, false);
});

test("演员按链接是否有组合条件决定默认性别", () => {
  const expression = {
    version: 1,
    expression: { type: "group", op: "and", children: [eq("has_image", true)] },
  };
  for (const [params, expected] of [
    [{}, eq("gender", "female")],
    [{ gender: [] }, undefined],
    [{ f: expression }, undefined],
    [{ f: expression, gender: ["male"] }, eq("gender", "male")],
    [{ f: expression, gender: ["female"] }, eq("gender", "female")],
  ] satisfies [unknown, FilterRule | undefined][]) {
    const search = stripDefaultActorGender(actorsBrowseSearchSchema.parse(params));
    const filter = readBrowseFilter("actor", search, actorQuickFilterNodes(search));
    const first = (
      filter.active ? filter.outerFilters : filter.editorValue?.terms.map(({ node }) => node)
    )?.[0];
    assert.deepEqual(first, expected);
  }

  const search = actorsBrowseSearchSchema.parse({
    gender: ["female"],
    height_min: 170,
    page: 3,
    q: "查询",
  });
  const cleared = applyActorExpression(search, undefined);
  const query = actorListQueryFromSearch(cleared);
  assert.equal(query.gender, undefined);
  assert.equal(query.height_min, undefined);
  assert.equal(query.search, "查询");
  assert.equal(cleared.page, 1);
});
