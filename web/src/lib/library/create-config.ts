/**
 * 「新增媒体库」表单的记忆.
 *
 * 记住上一次成功创建时填写的配置, 下次打开创建弹窗时作为初值. 记忆项是除 `name` / `path` /
 * `scan` 之外的全部字段: 前两者逐库不同, 后者是「这次要不要立即扫描」, 沿用上次的选择会静默
 * 触发一次扫描.
 *
 * 只声明记忆自身的形状, 不引用创建表单的初值类型 (`components/library/library-form.tsx` 的
 * `emptyLibraryForm`): 那份类型同时承载编辑态, 记忆只承载创建输入. `stores/ui.ts` 的 merge 按
 * 本 schema 校验持久化值, 非法项整体丢弃.
 */

import { z } from "zod";
import {
  DOWNLOADABLE_RESOURCES,
  LIBRARY_AUTOMATIONS,
  LIBRARY_INGESTS,
  LINK_MODES,
  MOVE_MODES,
} from "@/lib/exhaustive-maps";

export const libraryCreateConfigSchema = z.object({
  recursive: z.boolean(),
  patterns: z.string(),
  move_mode: z.enum(MOVE_MODES),
  link_template: z.string(),
  link_mode: z.enum(LINK_MODES),
  strm_content_template: z.string(),
  write_nfo: z.boolean(),
  copy_resources: z.array(z.enum(DOWNLOADABLE_RESOURCES)),
  trailer_pattern: z.string(),
  blacklist_patterns: z.string(),
  min_file_size: z.number(),
  subtitle_extensions: z.string(),
  video_template: z.string(),
  thumb_template: z.string(),
  poster_template: z.string(),
  fanart_template: z.string(),
  extrafanart_template: z.string(),
  nfo_template: z.string(),
  trailer_template: z.string(),
  subtitle_template: z.string(),
  automation: z.enum(LIBRARY_AUTOMATIONS),
  ingest: z.enum(LIBRARY_INGESTS),
  cloud_path: z.string(),
});

export type LibraryCreateConfig = z.infer<typeof libraryCreateConfigSchema>;

/** 从创建表单取值; 形参只要求含记忆项的那些字段, 因此不依赖表单的初值类型. */
export function libraryCreateConfigFromForm(form: LibraryCreateConfig): LibraryCreateConfig {
  return {
    recursive: form.recursive,
    patterns: form.patterns,
    move_mode: form.move_mode,
    link_template: form.link_template,
    link_mode: form.link_mode,
    strm_content_template: form.strm_content_template,
    write_nfo: form.write_nfo,
    copy_resources: form.copy_resources,
    trailer_pattern: form.trailer_pattern,
    blacklist_patterns: form.blacklist_patterns,
    min_file_size: form.min_file_size,
    subtitle_extensions: form.subtitle_extensions,
    video_template: form.video_template,
    thumb_template: form.thumb_template,
    poster_template: form.poster_template,
    fanart_template: form.fanart_template,
    extrafanart_template: form.extrafanart_template,
    nfo_template: form.nfo_template,
    trailer_template: form.trailer_template,
    subtitle_template: form.subtitle_template,
    automation: form.automation,
    ingest: form.ingest,
    cloud_path: form.cloud_path,
  };
}

/** 记忆存在时覆盖创建表单的初值; 无记忆则原样返回. */
export function applyLibraryCreateConfig<T extends LibraryCreateConfig>(
  base: T,
  config: LibraryCreateConfig | undefined,
): T {
  return config == null ? base : { ...base, ...config };
}
