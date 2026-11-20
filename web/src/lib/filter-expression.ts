import type { ParseKeys } from "i18next";
import type { ActorGender, FacetKind, FilterGroup, FilterNode } from "@/client/types.gen";
import { exhaustiveTuple } from "@/lib/exhaustive";
import { CONTENT_TYPES, FILE_DEFINITIONS, MOSAICS } from "@/lib/exhaustive-maps";

const filterActorGenders = exhaustiveTuple<ActorGender>()("female", "male", "unknown");

export type FilterEntity = "metadata" | "actor";

export type FilterFieldName = import("@/client/types.gen").FilterRule["field"];

export type FilterOperator = import("@/client/types.gen").FilterRule["operator"];

export type FilterValue = import("@/client/types.gen").FilterRule["value"];

export const FILTER_MAX_GROUP_DEPTH = 8;
export const FILTER_MAX_NODES = 256;
export const FILTER_MAX_ID_RULES = 200;
export const FILTER_MAX_VALUE_LENGTH = 200;

export interface FilterRule {
  type: "rule";
  field: FilterFieldName;
  operator: FilterOperator;
  value: FilterValue;
}

export type { FilterGroup, FilterNode } from "@/client/types.gen";

export type FormulaTerm = { join: FilterGroup["op"]; node: FilterRule | FilterFormula };

export interface FilterFormula {
  type: "group";
  terms: FormulaTerm[];
  negate?: boolean;
}

export function formulaGroup(terms: FormulaTerm[], negate?: boolean): FilterFormula {
  // 首项没有前置连接符, 统一存为 and.
  if (terms[0]?.join === "or") terms[0] = { ...terms[0], join: "and" };
  return negate && terms.length ? { type: "group", terms, negate } : { type: "group", terms };
}

export function formulaFromTree(group: FilterGroup): FilterFormula {
  return formulaGroup(
    group.children.map((node) => ({
      join: group.op,
      node: node.type === "group" ? formulaFromTree(node) : node,
    })),
    group.negate,
  );
}

export function filterRuleCount(group: FilterFormula): number {
  return group.terms.reduce(
    (count, { node }) => count + (node.type === "rule" ? 1 : filterRuleCount(node)),
    0,
  );
}

type MetadataKey = ParseKeys<"metadata">;

const genderLabelKeys: Record<ActorGender, MetadataKey> = {
  female: "browse.person.genderFemale",
  male: "browse.person.genderMale",
  unknown: "browse.person.genderUnknown",
};

export interface FilterOption {
  value: string;
  labelKey?: MetadataKey;
}

interface BaseField {
  field: FilterFieldName;
  labelKey: MetadataKey;
  section: "identity" | "profile" | "files";
  operators: readonly FilterOperator[];
}

export type FilterField = BaseField &
  (
    | { control: "text" | "date" | "cup" | "boolean" }
    | { control: "number"; min: number; max?: number }
    | { control: "enum"; options: readonly FilterOption[] }
    | { control: "facet"; kind: FacetKind }
    | { control: "library" }
  );

const metadataFields: readonly FilterField[] = [
  {
    field: "number",
    labelKey: "columns.number",
    section: "identity",
    operators: ["contains"],
    control: "text",
  },
  {
    field: "title",
    labelKey: "columns.title",
    section: "identity",
    operators: ["contains"],
    control: "text",
  },
  {
    field: "actor_id",
    labelKey: "browse.kinds.actor",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "actor",
  },
  {
    field: "director_id",
    labelKey: "browse.kinds.director",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "director",
  },
  {
    field: "tag_id",
    labelKey: "browse.kinds.tag",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "tag",
  },
  {
    field: "user_tag_id",
    labelKey: "browse.kinds.user_tag",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "user_tag",
  },
  {
    field: "studio_id",
    labelKey: "browse.kinds.studio",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "studio",
  },
  {
    field: "publisher_id",
    labelKey: "browse.kinds.publisher",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "publisher",
  },
  {
    field: "series_id",
    labelKey: "browse.kinds.series",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "series",
  },
  {
    field: "library_id",
    labelKey: "search.library",
    section: "files",
    operators: ["eq"],
    control: "library",
  },
  {
    field: "has_files",
    labelKey: "search.hasFiles",
    section: "files",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "has_subtitle",
    labelKey: "search.hasSubtitle",
    section: "files",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "vr",
    labelKey: "search.vr",
    section: "identity",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "uncensored",
    labelKey: "search.uncensored",
    section: "files",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "mosaic",
    labelKey: "search.mosaic",
    section: "files",
    operators: ["eq"],
    control: "enum",
    options: MOSAICS.map((value) => ({ value, labelKey: `search.mosaics.${value}` })),
  },
  {
    field: "definition",
    labelKey: "search.definition",
    section: "files",
    operators: ["eq"],
    control: "enum",
    options: FILE_DEFINITIONS.map((value) => ({ value })),
  },
  {
    field: "content_type",
    labelKey: "search.contentType",
    section: "identity",
    operators: ["eq"],
    control: "enum",
    options: CONTENT_TYPES.map((value) => ({ value, labelKey: `search.contentTypes.${value}` })),
  },
];

const actorFields: readonly FilterField[] = [
  {
    field: "name",
    labelKey: "advancedFilter.actorName",
    section: "identity",
    operators: ["contains"],
    control: "text",
  },
  {
    field: "alias",
    labelKey: "browse.person.aliases",
    section: "identity",
    operators: ["contains"],
    control: "text",
  },
  {
    field: "gender",
    labelKey: "browse.person.gender",
    section: "profile",
    operators: ["eq"],
    control: "enum",
    options: filterActorGenders.map((value) => ({
      value,
      labelKey: genderLabelKeys[value],
    })),
  },
  {
    field: "user_tag_id",
    labelKey: "browse.kinds.user_tag",
    section: "identity",
    operators: ["eq"],
    control: "facet",
    kind: "user_tag",
  },
  {
    field: "has_person",
    labelKey: "actors.filterPerson",
    section: "profile",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "has_image",
    labelKey: "actors.filterImage",
    section: "profile",
    operators: ["eq"],
    control: "boolean",
  },
  {
    field: "birthday",
    labelKey: "browse.person.birthday",
    section: "profile",
    operators: ["gte", "lte"],
    control: "date",
  },
  {
    field: "height",
    labelKey: "browse.person.height",
    section: "profile",
    operators: ["gte", "lte"],
    control: "number",
    min: 0,
  },
  {
    field: "bust",
    labelKey: "browse.person.bust",
    section: "profile",
    operators: ["gte", "lte"],
    control: "number",
    min: 0,
  },
  {
    field: "waist",
    labelKey: "browse.person.waist",
    section: "profile",
    operators: ["gte", "lte"],
    control: "number",
    min: 0,
  },
  {
    field: "hip",
    labelKey: "browse.person.hip",
    section: "profile",
    operators: ["gte", "lte"],
    control: "number",
    min: 0,
  },
  {
    field: "cup",
    labelKey: "browse.person.cup",
    section: "profile",
    operators: ["gte", "lte"],
    control: "cup",
  },
  {
    field: "birthplace",
    labelKey: "browse.person.birthplace",
    section: "profile",
    operators: ["contains"],
    control: "text",
  },
];

export function filterFieldsFor(entity: FilterEntity): readonly FilterField[] {
  return entity === "metadata" ? metadataFields : actorFields;
}

export function filterFieldFor(entity: FilterEntity, field: string): FilterField | undefined {
  return filterFieldsFor(entity).find((candidate) => candidate.field === field);
}

export function initialFieldValue(field: FilterField): FilterValue {
  if (field.control === "boolean") return true;
  if (field.control === "enum") return field.options[0]?.value ?? "";
  return "";
}

export function createFilterRule(field: FilterField): FilterRule {
  return {
    type: "rule",
    field: field.field,
    operator: field.operators[0] ?? "eq",
    value: initialFieldValue(field),
  };
}

export function filterRuleError(entity: FilterEntity, rule: FilterRule): MetadataKey | null {
  const field = filterFieldFor(entity, rule.field);
  if (!field) return "advancedFilter.errors.field";
  if (!field.operators.includes(rule.operator)) return "advancedFilter.errors.operator";
  const value = rule.value;
  if (typeof value === "string" && Array.from(value).length > FILTER_MAX_VALUE_LENGTH)
    return "advancedFilter.errors.textLength";
  switch (field.control) {
    case "boolean":
      return typeof value === "boolean" ? null : "advancedFilter.errors.required";
    case "enum":
      return field.options.some((option) => option.value === value)
        ? null
        : "advancedFilter.errors.required";
    case "facet":
    case "library":
      return typeof value === "number" && Number.isSafeInteger(value) && value > 0
        ? null
        : "advancedFilter.errors.selection";
    case "number":
      return typeof value === "number" &&
        Number.isSafeInteger(value) &&
        value >= field.min &&
        (field.max === undefined || value <= field.max)
        ? null
        : "advancedFilter.errors.number";
    case "date": {
      if (
        typeof value !== "string" ||
        !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        value.startsWith("0000-")
      )
        return "advancedFilter.errors.date";
      const date = new Date(`${value}T00:00:00Z`);
      return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
        ? null
        : "advancedFilter.errors.date";
    }
    case "cup":
      return typeof value === "string" && /^[A-Z]{1,2}$/.test(value)
        ? null
        : "advancedFilter.errors.cup";
    case "text":
      return typeof value === "string" && value.trim().length > 0
        ? null
        : "advancedFilter.errors.required";
  }
}

export const FILTER_MAX_ENCODED_LENGTH = 4096;

export type FilterExpressionErrorCode =
  | "json"
  | "version"
  | "shape"
  | "field"
  | "operator"
  | "value"
  | "depth"
  | "nodes"
  | "ids"
  | "length";

export class FilterExpressionError extends Error {
  readonly code: FilterExpressionErrorCode;
  readonly messageKey: MetadataKey;
  readonly path: string;

  constructor(code: FilterExpressionErrorCode, messageKey: MetadataKey, path = "expression") {
    super(`${messageKey}: ${path}`);
    this.name = "FilterExpressionError";
    this.code = code;
    this.messageKey = messageKey;
    this.path = path;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function onlyKeys(value: Record<string, unknown>, keys: readonly string[], path: string): void {
  if (Object.keys(value).some((key) => !keys.includes(key))) {
    throw new FilterExpressionError("shape", "advancedFilter.errors.shape", path);
  }
}

type Budget = { nodes: number; idRules: number };

function countNode(budget: Budget, path: string): void {
  if (++budget.nodes > FILTER_MAX_NODES)
    throw new FilterExpressionError("nodes", "advancedFilter.errors.nodes", path);
}

function checkDepth(depth: number, path: string): void {
  if (depth > FILTER_MAX_GROUP_DEPTH)
    throw new FilterExpressionError("depth", "advancedFilter.errors.depth", path);
}

function invalidShape(path: string): never {
  throw new FilterExpressionError("shape", "advancedFilter.errors.shape", path);
}

function readRule(
  value: Record<string, unknown>,
  entity: FilterEntity,
  budget: Budget,
  path: string,
): FilterRule {
  onlyKeys(value, ["type", "field", "operator", "value"], path);
  if (
    value.type !== "rule" ||
    typeof value.field !== "string" ||
    (value.operator !== "eq" &&
      value.operator !== "contains" &&
      value.operator !== "gte" &&
      value.operator !== "lte") ||
    (typeof value.value !== "string" &&
      typeof value.value !== "number" &&
      typeof value.value !== "boolean")
  ) {
    throw new FilterExpressionError("shape", "advancedFilter.errors.shape", path);
  }
  const field = filterFieldFor(entity, value.field);
  if (!field) throw new FilterExpressionError("field", "advancedFilter.errors.field", path);
  const rule: FilterRule = {
    type: "rule",
    field: field.field,
    operator: value.operator,
    value: value.value,
  };
  if (!field.operators.includes(rule.operator))
    throw new FilterExpressionError("operator", "advancedFilter.errors.operator", path);
  if (field.control === "cup" && typeof rule.value === "string")
    rule.value = rule.value.toUpperCase();
  const error = filterRuleError(entity, rule);
  if (error) throw new FilterExpressionError("value", error, path);
  if (field.control === "facet" || field.control === "library") {
    budget.idRules += 1;
    if (budget.idRules > FILTER_MAX_ID_RULES)
      throw new FilterExpressionError("ids", "advancedFilter.errors.ids", path);
  }
  return rule;
}

function checkEncodedLength(json: string): void {
  // TanStack 将可解析为 JSON 的字符串再次序列化, 预算必须包含外层引号与转义.
  if (encodeURIComponent(JSON.stringify(json)).length > FILTER_MAX_ENCODED_LENGTH) {
    throw new FilterExpressionError("length", "advancedFilter.errors.urlLength");
  }
}

function readFormulaNode(
  value: unknown,
  entity: FilterEntity,
  version: 1 | 2,
  depth: number,
  budget: Budget,
  path: string,
  parentJoin?: FormulaTerm["join"],
): FormulaTerm["node"] {
  countNode(budget, path);
  if (!isRecord(value)) return invalidShape(path);
  if (value.type !== "group") return readRule(value, entity, budget, path);
  checkDepth(depth, path);
  if (value.negate !== undefined && typeof value.negate !== "boolean") return invalidShape(path);
  const read = (node: unknown, at: string, join?: FormulaTerm["join"]) =>
    readFormulaNode(node, entity, version, depth + 1, budget, at, join);
  let terms: FormulaTerm[];
  if (version === 2) {
    onlyKeys(value, ["type", "terms", "negate"], path);
    if (!Array.isArray(value.terms)) return invalidShape(path);
    terms = value.terms.map((term: unknown, index) => {
      const at = path + ".terms[" + index + "]";
      if (
        !isRecord(term) ||
        (term.join !== "and" && term.join !== "or") ||
        (index === 0 && term.join !== "and")
      )
        return invalidShape(at);
      onlyKeys(term, ["join", "node"], at);
      return { join: term.join, node: read(term.node, at + ".node") };
    });
  } else {
    onlyKeys(value, ["type", "op", "children", "negate", "implicit"], path);
    const { op, children, implicit } = value;
    if (
      (op !== "and" && op !== "or") ||
      !Array.isArray(children) ||
      (implicit !== undefined &&
        (implicit !== true ||
          op !== "and" ||
          parentJoin !== "or" ||
          value.negate === true ||
          children.length < 2))
    )
      return invalidShape(path);
    terms = children.flatMap((child: unknown, index): FormulaTerm[] => {
      const node = read(child, path + ".children[" + index + "]", op);
      if (node.type === "group" && isRecord(child) && child.implicit === true) {
        return node.terms.map((term, i) => (i === 0 ? { ...term, join: op } : term));
      }
      return [{ join: op, node }];
    });
  }
  if (!terms.length)
    throw new FilterExpressionError("shape", "advancedFilter.errors.emptyGroup", path);
  return formulaGroup(terms, value.negate === true);
}

/** 查询树的深度与节点数包含按优先级生成的与组. */
export function compileFilterFormula(
  formula: FilterFormula | null,
  entity: FilterEntity,
): FilterGroup | null {
  if (!formula?.terms.length) return null;
  const budget = { nodes: 0, idRules: 0 };
  function compile(group: FilterFormula, depth: number): FilterGroup {
    checkDepth(depth, "expression");
    countNode(budget, "expression");
    const runs: FormulaTerm["node"][][] = [[]];
    for (const { join, node } of group.terms) {
      if (join === "or" && runs[runs.length - 1].length) runs.push([]);
      runs[runs.length - 1].push(node);
    }
    const read = (node: FormulaTerm["node"], parentDepth: number): FilterNode => {
      if (node.type === "group") return compile(node, parentDepth + 1);
      countNode(budget, "expression");
      return readRule({ ...node }, entity, budget, "expression");
    };
    const hasOr = runs.length > 1;
    return {
      type: "group",
      op: hasOr ? "or" : "and",
      children: hasOr
        ? runs.map((run): FilterNode => {
            if (run.length === 1) return read(run[0], depth);
            checkDepth(depth + 1, "expression");
            countNode(budget, "expression");
            return { type: "group", op: "and", children: run.map((node) => read(node, depth + 1)) };
          })
        : runs[0].map((node) => read(node, depth)),
      ...(group.negate ? { negate: true } : {}),
    };
  }
  return compile(formula, 1);
}

/** f 使用 JSON 字符串; URL 转义交给路由. */
export function parseFilterFormula(value: unknown, entity: FilterEntity): FilterFormula | null {
  if (value == null) return null;
  let decoded: unknown = value;
  if (typeof value === "string") {
    checkEncodedLength(value);
    try {
      decoded = JSON.parse(value);
    } catch {
      throw new FilterExpressionError("json", "advancedFilter.errors.json");
    }
  } else {
    let json: string;
    try {
      json = JSON.stringify(value);
    } catch {
      return invalidShape("filter");
    }
    if (json === undefined) return invalidShape("filter");
    checkEncodedLength(json);
  }
  if (!isRecord(decoded)) return invalidShape("filter");
  const { version } = decoded;
  if (version !== 1 && version !== 2)
    throw new FilterExpressionError("version", "advancedFilter.errors.version", "version");
  onlyKeys(decoded, version === 1 ? ["version", "expression"] : ["version", "formula"], "filter");
  if (version === 1 && decoded.expression === null) return null;
  const key = version === 1 ? "expression" : "formula";
  const node = readFormulaNode(decoded[key], entity, version, 1, { nodes: 0, idRules: 0 }, key);
  if (node.type !== "group") return invalidShape(key);
  return node;
}

export function serializeFilterFormula(
  formula: FilterFormula | null,
  entity: FilterEntity,
): string | undefined {
  if (!formula?.terms.length) return undefined;
  const normalized = readFormulaNode(formula, entity, 2, 1, { nodes: 0, idRules: 0 }, "formula");
  const json = JSON.stringify({ version: 2, formula: normalized });
  checkEncodedLength(json);
  return json;
}
