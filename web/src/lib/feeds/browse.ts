/**
 * 与后端订阅条目列表参数对齐 (snake_case).
 * 仅 UI 导航态保留 feed / group / q / page / nodedupe.
 *
 * schema 放在 lib 而不是路由里, 侧栏默认参数的白名单 (见 `lib/nav-defaults.ts`) 才能引用它.
 */

import { z } from "zod";

export const feedsSearchSchema = z.object({
  feed: z.coerce.number().int().positive().optional(),
  group: z.string().optional(),
  q: z.string().optional(),
  state: z.enum(["active", "ignored", "all"]).catch("active").default("active"),
  read: z.enum(["unread", "read", "all"]).catch("unread").default("unread"),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  nodedupe: z.union([z.literal("1"), z.literal("true"), z.literal(true)]).optional(),
});

export type FeedsSearch = z.infer<typeof feedsSearchSchema>;
