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
  LinkMode,
  LoggingConfig,
  MediaFileStatus,
  MediaSortField,
  MetadataField,
  MetadataSortField,
  RoutineType,
  ScanMode,
  SiteOutcomeKind,
  SortOrder,
  SubmitTaskData,
  TaskStatus,
  TaskType,
} from "@/client/types.gen";
import { exhaustiveTuple } from "./exhaustive";

// ============================================================================
// 类型派生
// ============================================================================

/** 可提交任务的完整 payload 联合. */
export type TaskPayload = SubmitTaskData["body"];

/** 可提交任务的 type 字段联合. */
export type SubmittableTaskType = TaskPayload["type"];

/** 提取特定 task type 对应的 payload 形状. */
export type PayloadFor<K extends SubmittableTaskType> = Extract<TaskPayload, { type: K }>;

/** 日志级别 - 与 `LoggingConfig.level` / WS 日志事件同源. */
export type LogLevel = NonNullable<LoggingConfig["level"]>;

// ============================================================================
// 运行时元组
// ============================================================================

/** `ScanMode` 的运行时枚举. */
export const SCAN_MODES = exhaustiveTuple<ScanMode>()("add", "remove");

/** `CacheKind` 的运行时枚举 (刮削可复用的缓存种类). */
export const CACHE_KINDS = exhaustiveTuple<CacheKind>()("metadata", "trans");

/** `ContentType` 的运行时枚举. */
export const CONTENT_TYPES = exhaustiveTuple<ContentType>()(
  "censored",
  "uncensored",
  "chinese",
  "western",
  "fc2",
  "amateur",
  "hentai",
);

/** `TaskStatus` 的运行时枚举. */
export const TASK_STATUSES = exhaustiveTuple<TaskStatus>()("queued", "running", "done", "failed");

/** `TaskType` 的运行时枚举. */
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

/** `DownloadableResource` 的运行时枚举 (刮削下载 / 整理复制). */
export const DOWNLOADABLE_RESOURCES = exhaustiveTuple<DownloadableResource>()(
  "thumb",
  "poster",
  "extrafanart",
  "trailer",
);

/** `LibraryAutomation` 的运行时枚举 (库发现侧自动化级别). */
export const LIBRARY_AUTOMATIONS = exhaustiveTuple<LibraryAutomation>()("none", "watch", "scrape");

/** `LinkMode` 的运行时枚举 (整理后链接入口). */
export const LINK_MODES = exhaustiveTuple<LinkMode>()("strm", "symlink");

/** `MediaFileStatus` 的运行时枚举. */
export const MEDIA_FILE_STATUSES = exhaustiveTuple<MediaFileStatus>()(
  "pending",
  "scraped",
  "failed",
  "skip",
);

/** `MediaSortField` 的运行时枚举. */
export const MEDIA_SORT_FIELDS = exhaustiveTuple<MediaSortField>()(
  "number",
  "path",
  "status",
  "size",
  "created_at",
  "updated_at",
);

/** `RoutineType` 的运行时枚举. */
export const ROUTINE_TYPES = exhaustiveTuple<RoutineType>()(
  "cleanup",
  "upscale",
  "r18_import",
  "rescrape",
);

/** `FacetKind` 的运行时枚举 (分类索引种类). */
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

/** `ActorSortField` 的运行时枚举. */
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

/** 可提交任务类型的运行时枚举. */
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

/** `SiteOutcomeKind` 的运行时枚举 (刮削站点结果分组顺序). */
export const SITE_OUTCOME_KINDS = exhaustiveTuple<SiteOutcomeKind>()("ok", "cache_hit", "failed");

/** `SortOrder` 的运行时枚举. */
export const SORT_ORDERS = exhaustiveTuple<SortOrder>()("asc", "desc");

/** `MetadataSortField` 的运行时枚举. */
export const METADATA_SORT_FIELDS = exhaustiveTuple<MetadataSortField>()(
  "number",
  "title",
  "studio",
  "release",
  "created_at",
  "updated_at",
  "file_count",
);

/** `FacetSortField` 的运行时枚举. */
export const FACET_SORT_FIELDS = exhaustiveTuple<FacetSortField>()("name", "count");

/** `MetadataField` 的运行时枚举 (合并 / 翻译等字段全集). */
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

/** `LoggingConfig.level` 的运行时枚举. */
export const LOG_LEVELS = exhaustiveTuple<LogLevel>()(
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
);
