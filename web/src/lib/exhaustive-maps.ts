/**
 * 集中定义所有 `exhaustiveTuple` 映射 - - 将 OpenAPI 联合类型转换为可在运行时
 * 迭代的只读元组. 当后端新增枚举成员时, 此处编译报错, 迫使前端同步.
 *
 * 此文件仅依赖 `@/client/types.gen` (生成的) 与 `./exhaustive` (工具函数),
 * 不依赖任何组件或路由模块, 保持 `lib/` 层的无环依赖.
 */

import type {
  ActorSortField,
  CacheKind,
  ContentType,
  DownloadableResource,
  FacetKind,
  FacetSortField,
  LibraryAutomation,
  LibraryIngest,
  LinkMode,
  LoggingConfig,
  MediaFileStatus,
  MediaSortField,
  MetadataField,
  MetadataSortField,
  Mosaic,
  RoutineType,
  ScanMode,
  SiteOutcomeKind,
  SortOrder,
  SubmitTaskData,
  TaskStatus,
  TaskType,
} from "@/client/types.gen";
import { exhaustiveTuple } from "./exhaustive";

export type TaskPayload = SubmitTaskData["body"];

export type SubmittableTaskType = TaskPayload["type"];

export type PayloadFor<K extends SubmittableTaskType> = Extract<TaskPayload, { type: K }>;

/** 与 `LoggingConfig.level` / WS 日志事件同源. */
export type LogLevel = NonNullable<LoggingConfig["level"]>;

export const SCAN_MODES = exhaustiveTuple<ScanMode>()("add", "remove");

export const CACHE_KINDS = exhaustiveTuple<CacheKind>()("metadata", "trans");

export const CONTENT_TYPES = exhaustiveTuple<ContentType>()(
  "censored",
  "uncensored",
  "chinese",
  "western",
  "fc2",
  "amateur",
  "hentai",
);

export const MOSAICS = exhaustiveTuple<Mosaic>()("uncensored", "cracked", "leaked");

/** 顺序即优先级 (高→低). 与后端 DEFINITION_VALUES 对齐. */
export const FILE_DEFINITIONS = exhaustiveTuple<
  "8K" | "4K" | "1440p" | "1080p" | "720p" | "480p" | "HD" | "SD"
>()("8K", "4K", "1440p", "1080p", "720p", "480p", "HD", "SD");

export const TASK_STATUSES = exhaustiveTuple<TaskStatus>()("queued", "running", "done", "failed");

export const TASK_TYPES = exhaustiveTuple<TaskType>()(
  "refresh",
  "organize",
  "cleanup",
  "scrape",
  "upscale",
  "r18_import",
  "actor_scrape",
  "rescrape",
);

export const DOWNLOADABLE_RESOURCES = exhaustiveTuple<DownloadableResource>()(
  "thumb",
  "poster",
  "extrafanart",
  "trailer",
);

export const LIBRARY_AUTOMATIONS = exhaustiveTuple<LibraryAutomation>()("none", "watch", "scrape");

export const LIBRARY_INGESTS = exhaustiveTuple<LibraryIngest>()("native", "clouddrive");

export const LINK_MODES = exhaustiveTuple<LinkMode>()("strm", "symlink");

export const MEDIA_FILE_STATUSES = exhaustiveTuple<MediaFileStatus>()(
  "pending",
  "scraped",
  "failed",
  "skip",
);

export const MEDIA_SORT_FIELDS = exhaustiveTuple<MediaSortField>()(
  "number",
  "path",
  "status",
  "size",
  "created_at",
  "updated_at",
);

export const ROUTINE_TYPES = exhaustiveTuple<RoutineType>()(
  "cleanup",
  "upscale",
  "r18_import",
  "rescrape",
);

export const FACET_KINDS = exhaustiveTuple<FacetKind>()(
  "actor",
  "director",
  "tag",
  "studio",
  "publisher",
  "series",
  "user_tag",
);

/** 分类浏览页 kind (演员已独立为 /actors). */
export const CATALOG_FACET_KINDS = exhaustiveTuple<Exclude<FacetKind, "actor">>()(
  "director",
  "tag",
  "studio",
  "publisher",
  "series",
  "user_tag",
);

export const ACTOR_SORT_FIELDS = exhaustiveTuple<ActorSortField>()(
  "name",
  "count",
  "updated_at",
  "has_image",
  "birthday",
  "height",
  "bust",
  "waist",
  "hip",
  "cup",
);

export const SUBMITTABLE_TASK_TYPES = exhaustiveTuple<SubmittableTaskType>()(
  "scrape",
  "refresh",
  "organize",
  "cleanup",
  "upscale",
  "r18_import",
  "actor_scrape",
  "rescrape",
);

/** 刮削站点结果分组顺序. */
export const SITE_OUTCOME_KINDS = exhaustiveTuple<SiteOutcomeKind>()("ok", "cache_hit", "failed");

export const SORT_ORDERS = exhaustiveTuple<SortOrder>()("asc", "desc");

export const METADATA_SORT_FIELDS = exhaustiveTuple<MetadataSortField>()(
  "number",
  "title",
  "studio",
  "release",
  "created_at",
  "updated_at",
  "file_count",
);

export const FACET_SORT_FIELDS = exhaustiveTuple<FacetSortField>()("name", "count");

export const METADATA_FIELDS = exhaustiveTuple<MetadataField>()(
  "title",
  "plot",
  "actors",
  "directors",
  "tags",
  "series",
  "release",
  "runtime",
  "publisher",
  "studio",
  "poster_urls",
  "thumb_urls",
  "trailer_urls",
  "extrafanart",
  "score",
);

export const LOG_LEVELS = exhaustiveTuple<LogLevel>()(
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
);
