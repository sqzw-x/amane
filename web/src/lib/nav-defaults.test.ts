import assert from "node:assert/strict";
import { test } from "node:test";
import { parseFilterFormula, serializeFilterFormula } from "@/lib/filter-expression";
import { actorListDefaultsSchema, metaListDefaultsSchema } from "@/lib/nav-defaults";

const valid = JSON.stringify({
  version: 1,
  expression: {
    type: "group",
    op: "and",
    children: [{ type: "rule", field: "has_subtitle", operator: "eq", value: true }],
  },
});

const badDate = JSON.stringify({
  version: 1,
  expression: {
    type: "group",
    op: "and",
    children: [{ type: "rule", field: "birthday", operator: "gte", value: "2020-13-45" }],
  },
});

test("条件组合随默认参数存取", () => {
  const meta = metaListDefaultsSchema.safeParse({ view: "list", sort_by: "number", f: valid });
  assert.equal(meta.success, true);
  assert.equal(meta.data?.f, valid);
  assert.equal(parseFilterFormula(meta.data?.f, "metadata") !== null, true);

  const actor = actorListDefaultsSchema.safeParse({
    f: valid.replace("has_subtitle", "has_image"),
  });
  assert.equal(actor.success, true);
  const current = serializeFilterFormula(parseFilterFormula(valid, "metadata"), "metadata");
  assert.equal(metaListDefaultsSchema.safeParse({ f: current }).data?.f, current);
  assert.equal(actorListDefaultsSchema.safeParse({ f: current }).success, false);
});

test("解不出的条件组合整份丢弃", () => {
  assert.equal(metaListDefaultsSchema.safeParse({ f: badDate }).success, false);
  assert.equal(metaListDefaultsSchema.safeParse({ f: "{" }).success, false);
  assert.equal(
    metaListDefaultsSchema.safeParse({ f: valid.replace("has_subtitle", "height") }).success,
    false,
  );
  assert.equal(actorListDefaultsSchema.safeParse({ f: valid }).success, false);
});
