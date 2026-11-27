/**
 * 与后端 ActorBrowseParams 对齐 (snake_case).
 * 仅 UI 导航态保留 q / view / page.
 */

import type { ParseKeys } from "i18next";
import { z } from "zod";
import type { ActorGender, ListActorsData } from "@/client/types.gen";
import type { FilterNode } from "@/lib/filter-expression";
import { ACTOR_SORT_FIELDS, SORT_ORDERS } from "@/lib/exhaustive-maps";

type ActorListQuery = NonNullable<ListActorsData["query"]>;

export const ACTOR_GENDERS = [
  "female",
  "male",
  "unknown",
] as const satisfies readonly ActorGender[];

/** `/actors` 未带 gender 时的默认筛选; 从 URL 剥掉. 显式清空写入 `[]` 表示不限. */
export const DEFAULT_ACTOR_GENDER_FILTER = ["female"] as const satisfies readonly ActorGender[];

const ACTOR_FILTER_KEYS = [
  "has_person",
  "has_image",
  "user_tag_id",
  "gender",
  "birthday_min",
  "birthday_max",
  "height_min",
  "height_max",
  "bust_min",
  "bust_max",
  "waist_min",
  "waist_max",
  "hip_min",
  "hip_max",
  "cup_min",
  "cup_max",
  "birthplace",
] as const;

export type ActorFilterKey = (typeof ACTOR_FILTER_KEYS)[number];

export type ActorFilterPatch = Pick<ActorsBrowseSearch, ActorFilterKey>;
type ActorFilterValues = ActorFilterPatch & { gender: ActorGender[] };

export const ACTOR_RANGE_FILTERS = (
  ["birthday", "height", "cup", "bust", "waist", "hip"] as const
).map((field) => ({
  min: `${field}_min` satisfies ActorFilterKey,
  max: `${field}_max` satisfies ActorFilterKey,
  labelKey: `browse.person.${field}` satisfies ParseKeys<"metadata">,
}));

function isActorGender(value: string): value is ActorGender {
  return ACTOR_GENDERS.some((gender) => gender === value);
}

function coerceGenderList(value: unknown): ActorGender[] | undefined {
  if (value == null || value === "") return undefined;
  const raw = Array.isArray(value) ? value : [value];
  if (raw.length === 0) return [];
  const out: ActorGender[] = [];
  const seen = new Set<ActorGender>();
  for (const item of raw) {
    if (typeof item !== "string" || !isActorGender(item)) continue;
    if (seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out.length > 0 ? out : undefined;
}

function coerceOptionalInt(value: unknown): number | undefined {
  if (value == null || value === "") return undefined;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(n) || n < 0) return undefined;
  return n;
}

function coerceOptionalStr(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

const genderListSchema = z.preprocess(coerceGenderList, z.array(z.enum(ACTOR_GENDERS)).optional());
const optionalIntSchema = z.preprocess(
  coerceOptionalInt,
  z.number().int().nonnegative().optional(),
);
const optionalStrSchema = z.preprocess(coerceOptionalStr, z.string().optional());

const actorFilterSearchSchema = z.object({
  has_person: z.enum(["true", "false"]).optional(),
  has_image: z.enum(["true", "false"]).optional(),
  user_tag_id: optionalIntSchema,
  gender: genderListSchema,
  birthday_min: optionalStrSchema,
  birthday_max: optionalStrSchema,
  height_min: optionalIntSchema,
  height_max: optionalIntSchema,
  bust_min: optionalIntSchema,
  bust_max: optionalIntSchema,
  waist_min: optionalIntSchema,
  waist_max: optionalIntSchema,
  hip_min: optionalIntSchema,
  hip_max: optionalIntSchema,
  cup_min: optionalStrSchema,
  cup_max: optionalStrSchema,
  birthplace: optionalStrSchema,
});

export const actorsBrowseSearchSchema = z.object({
  q: z.string().optional(),
  f: z.unknown().optional(),
  view: z.enum(["grid", "list"]).catch("grid").default("grid"),
  sort_by: z.enum(ACTOR_SORT_FIELDS).catch("count").default("count"),
  order: z.enum(SORT_ORDERS).catch("desc").default("desc"),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  saved_query_id: z.coerce.number().int().positive().optional(),
  ...actorFilterSearchSchema.shape,
});

export type ActorsBrowseSearch = z.infer<typeof actorsBrowseSearchSchema>;

/** 组合筛选只接受显式性别; 普通列表缺省为女演员, 空数组表示不限. */
function resolvedActorGenders(search: ActorsBrowseSearch): ActorGender[] {
  return search.gender ?? (search.f === undefined ? [...DEFAULT_ACTOR_GENDER_FILTER] : []);
}

function parseTriBool(value: ActorFilterPatch["has_person"]): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export function actorFilterValuesFromSearch(search: ActorsBrowseSearch): ActorFilterValues {
  return { ...search, gender: resolvedActorGenders(search) };
}

export function actorQuickFilterNodes(search: ActorsBrowseSearch): FilterNode[] {
  const nodes: FilterNode[] = [];
  const gender = resolvedActorGenders(search);
  if (gender.length > 0) {
    const children: FilterNode[] = gender.map((value) => ({
      type: "rule",
      field: "gender",
      operator: "eq",
      value,
    }));
    nodes.push(children.length === 1 ? children[0] : { type: "group", op: "or", children });
  }
  for (const field of ["has_person", "has_image"] as const) {
    if (search[field] != null)
      nodes.push({ type: "rule", field, operator: "eq", value: search[field] === "true" });
  }
  if (search.user_tag_id != null)
    nodes.push({ type: "rule", field: "user_tag_id", operator: "eq", value: search.user_tag_id });
  for (const field of ["birthday", "height", "bust", "waist", "hip", "cup"] as const) {
    const min = search[`${field}_min`];
    const max = search[`${field}_max`];
    if (min != null) nodes.push({ type: "rule", field, operator: "gte", value: min });
    if (max != null) nodes.push({ type: "rule", field, operator: "lte", value: max });
  }
  if (search.birthplace)
    nodes.push({
      type: "rule",
      field: "birthplace",
      operator: "contains",
      value: search.birthplace,
    });
  return nodes;
}

export function applyActorExpression(
  search: ActorsBrowseSearch,
  f: string | undefined,
): ActorsBrowseSearch {
  return {
    ...clearActorFilterKeys(search, ACTOR_FILTER_KEYS),
    gender: f === undefined ? [] : undefined,
    f,
  };
}

export function actorListQueryFromSearch(search: ActorsBrowseSearch): ActorListQuery {
  const gender = resolvedActorGenders(search);
  return {
    search: search.q || undefined,
    sort_by: search.sort_by,
    order: search.order,
    has_person: parseTriBool(search.has_person),
    has_image: parseTriBool(search.has_image),
    gender: gender.length > 0 ? gender : undefined,
    user_tag_ids: search.user_tag_id != null ? [search.user_tag_id] : undefined,
    birthday_min: search.birthday_min,
    birthday_max: search.birthday_max,
    height_min: search.height_min,
    height_max: search.height_max,
    bust_min: search.bust_min,
    bust_max: search.bust_max,
    waist_min: search.waist_min,
    waist_max: search.waist_max,
    hip_min: search.hip_min,
    hip_max: search.hip_max,
    cup_min: search.cup_min,
    cup_max: search.cup_max,
    birthplace: search.birthplace,
    ...(search.saved_query_id != null ? { saved_query_id: search.saved_query_id } : {}),
  };
}

/** 相对 ``asOf`` 回推 N 年的日历日 YYYY-MM-DD (与周岁边界一致). */
function calendarDateYearsAgo(years: number, asOf: Date = new Date()): string {
  const d = new Date(asOf.getFullYear() - years, asOf.getMonth(), asOf.getDate());
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * 年龄范围 → 生日范围.
 * age ≥ min → birthday ≤ today−min; age ≤ max → birthday ≥ today−max.
 */
export function birthdayBoundsFromAge(
  ageMin: number | undefined,
  ageMax: number | undefined,
  asOf: Date = new Date(),
): Pick<ActorFilterValues, "birthday_min" | "birthday_max"> {
  let lo = ageMin;
  let hi = ageMax;
  if (lo != null && hi != null && lo > hi) {
    const tmp = lo;
    lo = hi;
    hi = tmp;
  }
  return {
    birthday_max: lo != null ? calendarDateYearsAgo(lo, asOf) : undefined,
    birthday_min: hi != null ? calendarDateYearsAgo(hi, asOf) : undefined,
  };
}

export function clearActorFilterKeys(
  prev: ActorsBrowseSearch,
  keys: readonly ActorFilterKey[],
): ActorsBrowseSearch {
  const next: ActorsBrowseSearch = { ...prev, page: 1 };
  for (const key of keys) next[key] = undefined;
  return next;
}

function actorGenderFilterIsDefault(gender: readonly ActorGender[]): boolean {
  return (
    gender.length === DEFAULT_ACTOR_GENDER_FILTER.length &&
    gender.every((g, i) => g === DEFAULT_ACTOR_GENDER_FILTER[i])
  );
}

export function stripDefaultActorGender(search: ActorsBrowseSearch): ActorsBrowseSearch {
  if (search.f === undefined && search.gender && actorGenderFilterIsDefault(search.gender)) {
    return { ...search, gender: undefined };
  }
  return search;
}

function hasNonGenderActorFilters(filters: ActorFilterValues): boolean {
  if (filters.has_person != null || filters.has_image != null) return true;
  if (filters.user_tag_id != null) return true;
  if (filters.birthplace != null) return true;
  for (const range of ACTOR_RANGE_FILTERS) {
    if (filters[range.min] != null || filters[range.max] != null) return true;
  }
  return false;
}

export function hasActiveActorFilters(filters: ActorFilterValues): boolean {
  if (filters.gender.length > 0) return true;
  return hasNonGenderActorFilters(filters);
}

/** 相对页面默认 (仅女演员) 是否另有筛选 — 用于图标高亮 / 展开面板, 不含默认 gender. */
export function hasNonDefaultActorFilters(filters: ActorFilterValues): boolean {
  if (!actorGenderFilterIsDefault(filters.gender)) return true;
  return hasNonGenderActorFilters(filters);
}

export function formatActorRangeChip(
  label: string,
  min: string | number | undefined,
  max: string | number | undefined,
): string {
  if (min != null && max != null) return `${label} ${min}–${max}`;
  if (min != null) return `${label} ≥${min}`;
  if (max != null) return `${label} ≤${max}`;
  return label;
}
