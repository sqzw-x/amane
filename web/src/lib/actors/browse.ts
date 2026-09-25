/**
 * 与后端 ActorBrowseParams 对齐 (snake_case).
 * 仅 UI 导航态保留 q / view / page.
 */

import type { ParseKeys } from "i18next";
import { z } from "zod";
import type { ActorGender, ListActorsData } from "@/client/types.gen";
import { ACTOR_SORT_FIELDS, SORT_ORDERS } from "@/lib/exhaustive-maps";

export type ActorListQuery = NonNullable<ListActorsData["query"]>;

export const ACTOR_GENDERS = [
  "female",
  "male",
  "unknown",
] as const satisfies readonly ActorGender[];

/** `/actors` 未带 gender 时的默认筛选; 从 URL 剥掉. 显式清空写入 `[]` 表示不限. */
export const DEFAULT_ACTOR_GENDER_FILTER = ["female"] as const satisfies readonly ActorGender[];

export const ACTOR_FILTER_KEYS = [
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

export type ActorTriBool = "true" | "false";

/** 面板与芯片共用的筛选值.
 * gender 缺省为 ``DEFAULT_ACTOR_GENDER_FILTER``; `[]` 表示不限.
 * ``age_*`` 仅草稿 UI: 应用时换算为 ``birthday_*``, 不写入 URL/API.
 */
export type ActorFilterValues = {
  gender: ActorGender[];
  has_person?: ActorTriBool;
  has_image?: ActorTriBool;
  /** 用户标签 id; 不设多选, 与面板其余单选控件同形 */
  user_tag_id?: number;
  /** 周岁下界 - 仅面板草稿, 应用时 → birthday_max */
  age_min?: number;
  /** 周岁上界 - 仅面板草稿, 应用时 → birthday_min */
  age_max?: number;
  birthday_min?: string;
  birthday_max?: string;
  height_min?: number;
  height_max?: number;
  bust_min?: number;
  bust_max?: number;
  waist_min?: number;
  waist_max?: number;
  hip_min?: number;
  hip_max?: number;
  cup_min?: string;
  cup_max?: string;
  birthplace?: string;
};

export type ActorFilterPatch = Partial<ActorFilterValues>;

type MetadataKey = ParseKeys<"metadata">;

export type ActorRangeFilterKind = "date" | "int" | "cup";

export const ACTOR_RANGE_FILTERS = [
  {
    min: "birthday_min",
    max: "birthday_max",
    labelKey: "browse.person.birthday",
    kind: "date",
  },
  {
    min: "height_min",
    max: "height_max",
    labelKey: "browse.person.height",
    kind: "int",
  },
  {
    min: "cup_min",
    max: "cup_max",
    labelKey: "browse.person.cup",
    kind: "cup",
  },
  {
    min: "bust_min",
    max: "bust_max",
    labelKey: "browse.person.bust",
    kind: "int",
  },
  {
    min: "waist_min",
    max: "waist_max",
    labelKey: "browse.person.waist",
    kind: "int",
  },
  {
    min: "hip_min",
    max: "hip_max",
    labelKey: "browse.person.hip",
    kind: "int",
  },
] as const satisfies readonly {
  min: ActorFilterKey;
  max: ActorFilterKey;
  labelKey: MetadataKey;
  kind: ActorRangeFilterKind;
}[];

export type ActorRangeFilter = (typeof ACTOR_RANGE_FILTERS)[number];

function isActorGender(value: string): value is ActorGender {
  return (ACTOR_GENDERS as readonly string[]).includes(value);
}

export function coerceGenderList(value: unknown): ActorGender[] | undefined {
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

export function coerceOptionalInt(value: unknown): number | undefined {
  if (value == null || value === "") return undefined;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(n) || n < 0) return undefined;
  return n;
}

export function coerceOptionalStr(value: unknown): string | undefined {
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

export const actorFilterSearchSchema = z.object({
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
  view: z.enum(["grid", "list"]).catch("grid").default("grid"),
  sort_by: z.enum(ACTOR_SORT_FIELDS).catch("count").default("count"),
  order: z.enum(SORT_ORDERS).catch("desc").default("desc"),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  saved_query_id: z.coerce.number().int().positive().optional(),
  ...actorFilterSearchSchema.shape,
});

export type ActorsBrowseSearch = z.infer<typeof actorsBrowseSearchSchema>;

/** URL 未带 gender → 默认女演员; `[]` 表示不限. */
function resolvedActorGenders(search: Pick<ActorsBrowseSearch, "gender">): ActorGender[] {
  return search.gender ?? [...DEFAULT_ACTOR_GENDER_FILTER];
}

function parseTriBool(value: ActorTriBool | undefined): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export function actorFilterValuesFromSearch(search: ActorsBrowseSearch): ActorFilterValues {
  return {
    gender: resolvedActorGenders(search),
    has_person: search.has_person,
    has_image: search.has_image,
    user_tag_id: search.user_tag_id,
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

export function mergeActorFilterPatch(
  prev: ActorsBrowseSearch,
  patch: ActorFilterPatch,
): ActorsBrowseSearch {
  const next: ActorsBrowseSearch = { ...prev, page: 1 };
  if ("gender" in patch) {
    next.gender = patch.gender ?? [];
  }
  if ("has_person" in patch) next.has_person = patch.has_person;
  if ("has_image" in patch) next.has_image = patch.has_image;
  if ("user_tag_id" in patch) next.user_tag_id = patch.user_tag_id;
  if ("birthday_min" in patch) next.birthday_min = patch.birthday_min;
  if ("birthday_max" in patch) next.birthday_max = patch.birthday_max;
  if ("height_min" in patch) next.height_min = patch.height_min;
  if ("height_max" in patch) next.height_max = patch.height_max;
  if ("bust_min" in patch) next.bust_min = patch.bust_min;
  if ("bust_max" in patch) next.bust_max = patch.bust_max;
  if ("waist_min" in patch) next.waist_min = patch.waist_min;
  if ("waist_max" in patch) next.waist_max = patch.waist_max;
  if ("hip_min" in patch) next.hip_min = patch.hip_min;
  if ("hip_max" in patch) next.hip_max = patch.hip_max;
  if ("cup_min" in patch) next.cup_min = patch.cup_min;
  if ("cup_max" in patch) next.cup_max = patch.cup_max;
  if ("birthplace" in patch) next.birthplace = patch.birthplace;
  return next;
}

export function replaceActorFilters(
  prev: ActorsBrowseSearch,
  filters: ActorFilterValues,
): ActorsBrowseSearch {
  return {
    ...prev,
    page: 1,
    gender: filters.gender,
    has_person: filters.has_person,
    has_image: filters.has_image,
    user_tag_id: filters.user_tag_id,
    birthday_min: filters.birthday_min,
    birthday_max: filters.birthday_max,
    height_min: filters.height_min,
    height_max: filters.height_max,
    bust_min: filters.bust_min,
    bust_max: filters.bust_max,
    waist_min: filters.waist_min,
    waist_max: filters.waist_max,
    hip_min: filters.hip_min,
    hip_max: filters.hip_max,
    cup_min: filters.cup_min,
    cup_max: filters.cup_max,
    birthplace: filters.birthplace,
  };
}

export function cloneActorFilterValues(filters: ActorFilterValues): ActorFilterValues {
  return {
    ...filters,
    gender: [...filters.gender],
  };
}

/** 相对 ``asOf`` 回推 N 年的日历日 YYYY-MM-DD (与周岁边界一致). */
export function calendarDateYearsAgo(years: number, asOf: Date = new Date()): string {
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

/** 应用前规范化: 去空白; 若填了年龄则覆盖生日并删除 age_*. */
function trimFilterText(value: string | undefined): string | undefined {
  if (value == null) return undefined;
  const text = value.trim();
  return text || undefined;
}

export function normalizeActorFilterValues(
  filters: ActorFilterValues,
  asOf: Date = new Date(),
): ActorFilterValues {
  const hasAge = filters.age_min != null || filters.age_max != null;
  const fromAge = hasAge
    ? birthdayBoundsFromAge(filters.age_min, filters.age_max, asOf)
    : {
        birthday_min: trimFilterText(filters.birthday_min),
        birthday_max: trimFilterText(filters.birthday_max),
      };

  return {
    gender: [...filters.gender],
    has_person: filters.has_person,
    has_image: filters.has_image,
    user_tag_id: filters.user_tag_id,
    birthday_min: fromAge.birthday_min,
    birthday_max: fromAge.birthday_max,
    height_min: filters.height_min,
    height_max: filters.height_max,
    bust_min: filters.bust_min,
    bust_max: filters.bust_max,
    waist_min: filters.waist_min,
    waist_max: filters.waist_max,
    hip_min: filters.hip_min,
    hip_max: filters.hip_max,
    cup_min: trimFilterText(filters.cup_min)?.toUpperCase(),
    cup_max: trimFilterText(filters.cup_max)?.toUpperCase(),
    birthplace: trimFilterText(filters.birthplace),
  };
}

export function actorFiltersEqual(a: ActorFilterValues, b: ActorFilterValues): boolean {
  if (a.gender.length !== b.gender.length) return false;
  for (let i = 0; i < a.gender.length; i++) {
    if (a.gender[i] !== b.gender[i]) return false;
  }
  return (
    a.has_person === b.has_person &&
    a.has_image === b.has_image &&
    a.user_tag_id === b.user_tag_id &&
    a.age_min === b.age_min &&
    a.age_max === b.age_max &&
    a.birthday_min === b.birthday_min &&
    a.birthday_max === b.birthday_max &&
    a.height_min === b.height_min &&
    a.height_max === b.height_max &&
    a.bust_min === b.bust_min &&
    a.bust_max === b.bust_max &&
    a.waist_min === b.waist_min &&
    a.waist_max === b.waist_max &&
    a.hip_min === b.hip_min &&
    a.hip_max === b.hip_max &&
    a.cup_min === b.cup_min &&
    a.cup_max === b.cup_max &&
    a.birthplace === b.birthplace
  );
}

export function actorFilterFingerprint(filters: ActorFilterValues): string {
  return JSON.stringify(normalizeActorFilterValues(filters));
}

export function clearActorFilterKeys(
  prev: ActorsBrowseSearch,
  keys: readonly ActorFilterKey[],
): ActorsBrowseSearch {
  const next: ActorsBrowseSearch = { ...prev, page: 1 };
  for (const key of keys) {
    if (key === "gender") {
      next.gender = [];
    } else {
      next[key] = undefined;
    }
  }
  return next;
}

function actorGenderFilterIsDefault(gender: readonly ActorGender[]): boolean {
  return (
    gender.length === DEFAULT_ACTOR_GENDER_FILTER.length &&
    gender.every((g, i) => g === DEFAULT_ACTOR_GENDER_FILTER[i])
  );
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

export function rangeValue(
  filters: ActorFilterValues,
  key: ActorFilterKey,
): string | number | undefined {
  if (key === "gender" || key === "has_person" || key === "has_image") return undefined;
  const value = filters[key];
  if (typeof value === "string" || typeof value === "number") return value;
  return undefined;
}
