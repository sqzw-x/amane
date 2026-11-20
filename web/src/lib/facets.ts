/** catalog / meta 共用: FacetKind ↔ 列表过滤查询参数名. */

import {
  IconBooks,
  IconBuilding,
  IconBuildingStore,
  IconTag,
  IconTags,
  IconUsers,
  IconVideo,
} from "@tabler/icons-react";
import type { FacetKind, ListMetadataData } from "@/client/types.gen";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { FACET_KINDS } from "@/lib/exhaustive-maps";

export const FACET_KIND_ICON = exhaustiveRecord<FacetKind>()({
  actor: IconUsers,
  director: IconVideo,
  tag: IconTag,
  studio: IconBuilding,
  publisher: IconBuildingStore,
  series: IconBooks,
  user_tag: IconTags,
});

export const FACET_FILTER_PARAM = {
  actor: "actor_id",
  director: "director_id",
  tag: "tag_id",
  studio: "studio_id",
  publisher: "publisher_id",
  series: "series_id",
  user_tag: "user_tag_id",
} as const satisfies Record<FacetKind, keyof NonNullable<ListMetadataData["query"]>>;

export type FacetFilterParam = (typeof FACET_FILTER_PARAM)[FacetKind];

/** 用户 tag 下拉: 经 facets 目录, 一次拉满 (上限与列表一致). */
export const USER_TAG_FACET_LIST = {
  path: { kind: "user_tag" as const },
  query: { limit: 1000 },
};

export type FacetFilters = {
  [K in FacetFilterParam]?: number[];
};

export function metaSearchForFacet(kind: FacetKind, id: number): FacetFilters {
  const filters: FacetFilters = {};
  filters[FACET_FILTER_PARAM[kind]] = [id];
  return filters;
}

export function removeFacetId(filters: FacetFilters, kind: FacetKind, id: number): FacetFilters {
  const param = FACET_FILTER_PARAM[kind];
  const next = (filters[param] ?? []).filter((x) => x !== id);
  return { ...filters, [param]: next.length > 0 ? next : undefined };
}

export function activeFacetFilters(filters: FacetFilters): Array<{ kind: FacetKind; id: number }> {
  const entries: Array<{ kind: FacetKind; id: number }> = [];
  for (const kind of FACET_KINDS) {
    const param = FACET_FILTER_PARAM[kind];
    for (const id of filters[param] ?? []) {
      entries.push({ kind, id });
    }
  }
  return entries;
}

/**
 * URL search 中的 facet id 列表: 兼容单值与数组.
 * TanStack Router / 查询串可能给出 number | string | (number|string)[].
 */
export function coerceIdList(value: unknown): number[] | undefined {
  if (value == null || value === "") return undefined;
  const raw = Array.isArray(value) ? value : [value];
  const ids: number[] = [];
  const seen = new Set<number>();
  for (const item of raw) {
    const n = typeof item === "number" ? item : Number(item);
    if (!Number.isInteger(n) || n <= 0 || seen.has(n)) continue;
    seen.add(n);
    ids.push(n);
  }
  return ids.length > 0 ? ids : undefined;
}
