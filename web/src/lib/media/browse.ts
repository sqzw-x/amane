/**
 * 与后端 MetadataListParams 对齐 (snake_case).
 * 仅 UI 导航态保留 q / view / page / saved_query_id.
 *
 * 与 `lib/actors/browse.ts` 同形: schema 放在 lib 而不是路由里, 侧栏默认参数的白名单
 * (见 `lib/nav-defaults.ts`) 才能引用它 —— 从侧栏组件静态导入路由模块会把该路由的 chunk
 * 并入入口 chunk.
 */

import type { ParseKeys } from "i18next";
import { z } from "zod";
import type { MetadataSortField } from "@/client/types.gen";
import {
  CONTENT_TYPES,
  FILE_DEFINITIONS,
  METADATA_SORT_FIELDS,
  MOSAICS,
  SORT_ORDERS,
} from "@/lib/exhaustive-maps";
import { coerceIdList } from "@/lib/facets";

const idListSchema = z.preprocess(coerceIdList, z.array(z.number().int().positive()).optional());

/** 片库与演员详情出演作品共用的排序项; 数组顺序即菜单显示顺序. */
export const METADATA_SORT_OPTIONS = [
  { value: "updated_at", labelKey: "columns.updated" },
  { value: "created_at", labelKey: "columns.created" },
  { value: "number", labelKey: "columns.number" },
  { value: "title", labelKey: "columns.title" },
  { value: "studio", labelKey: "columns.studio" },
  { value: "release", labelKey: "columns.release" },
  { value: "file_count", labelKey: "columns.fileCount" },
] as const satisfies readonly {
  value: MetadataSortField;
  labelKey: ParseKeys<"metadata">;
}[];

/** 排序记忆值: 演员详情页的作品排序存于 ui store, 读取时经此校验, 非法整值丢弃. */
export const metadataSortPreferenceSchema = z.object({
  sort_by: z.enum(METADATA_SORT_FIELDS),
  order: z.enum(SORT_ORDERS),
});

export type MetadataSortPreference = z.infer<typeof metadataSortPreferenceSchema>;

/** 与后端列表默认一致: 无记忆时出演作品的顺序不变. */
export const DEFAULT_METADATA_SORT_PREFERENCE: MetadataSortPreference = {
  sort_by: "updated_at",
  order: "desc",
};

export const metaSearchSchema = z.object({
  q: z.string().optional(),
  view: z.enum(["grid", "list"]).catch("grid").default("grid"),
  sort_by: z.enum(METADATA_SORT_FIELDS).optional(),
  order: z.enum(SORT_ORDERS).optional(),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  actor_id: idListSchema,
  director_id: idListSchema,
  tag_id: idListSchema,
  studio_id: idListSchema,
  publisher_id: idListSchema,
  series_id: idListSchema,
  user_tag_id: idListSchema,
  has_files: z.enum(["true", "false"]).optional(),
  has_subtitle: z.enum(["true", "false"]).optional(),
  uncensored: z.enum(["true", "false"]).optional(),
  mosaic: z.enum(MOSAICS).optional(),
  definition: z.enum(FILE_DEFINITIONS).optional(),
  content_type: z.enum(CONTENT_TYPES).optional(),
  saved_query_id: z.coerce.number().int().positive().optional(),
});

export type MetaSearch = z.infer<typeof metaSearchSchema>;
